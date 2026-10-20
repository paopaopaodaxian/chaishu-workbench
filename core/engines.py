"""模型连接：Claude Code 与 Codex 两个本地 CLI 的适配层。

原则（设计稿 P09 / 验收 3）：
- 只走 CLI 官方登录；用户能看到实际连接的身份与工作区，无法判断时提示「请核对工作区」。
- 默认屏蔽环境变量中的 API Key / 自定义网关，避免无提示地使用其他凭证来源；用户可在设置中显式允许。
- 验证失败不自动切换凭证或模型；本工具不保存任何令牌。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import db

HOME = Path.home()
EXTRA_PATHS = ["~/.claude/local", "~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
               "~/.npm-global/bin", "~/.bun/bin", "~/.volta/bin", "~/.cargo/bin"]


class EngineError(Exception):
    """kind: login / quota / model / network / format / cancelled / timeout / unknown"""

    def __init__(self, kind: str, message: str, hint: str = ""):
        super().__init__(message)
        self.kind, self.message, self.hint = kind, message, hint


FRIENDLY = {
    "login": "需要重新登录",
    "quota": "额度已用完或触发频率限制",
    "model": "所选模型不可用或无权限",
    "network": "网络或服务暂时异常",
    "format": "模型输出未通过格式校验",
    "timeout": "调用超时",
    "cancelled": "已取消",
    "unknown": "调用失败",
}


def find_bin(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for d in EXTRA_PATHS:
        c = Path(os.path.expanduser(d)) / name
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return None


def classify(text: str, status: int | None = None) -> EngineError:
    t = (text or "").lower()
    msg = (text or "").strip()[-600:]

    def has(*ks):
        return any(k in t for k in ks)

    if status in (401, 403) or has("not logged in", "please log in", "please login", "log in again", "/login",
                                    "unauthorized", "authentication_error", "invalid api key", "invalid x-api-key",
                                    "token expired", "token has expired", "refresh token", "oauth token", "401 "):
        return EngineError("login", msg)
    if status == 429 or has("usage limit", "rate limit", "rate_limit", "quota", "429", "limit reached",
                            "too many requests", "credit balance", "insufficient_quota", "hit your limit"):
        m = re.search(r"((?:try again|resets?|reset at|available again)[^\n]{0,80})", text or "", re.I)
        return EngineError("quota", msg, m.group(1).strip() if m else "")
    if (status in (400, 404) and "model" in t) or ("model" in t and has(
            "not found", "does not exist", "not available", "not supported", "do not have access",
            "don't have access", "unrecognized", "invalid model", "may not exist")):
        return EngineError("model", msg)
    if (status or 0) >= 500 or has("timeout", "timed out", "network", "econn", "enotfound", "socket", "stream disconnected",
                                   "connection", "overloaded", "502", "503", "504", "529", "internal server error",
                                   "dns", "proxy", "tls", "certificate"):
        return EngineError("network", msg)
    return EngineError("unknown", msg or "未知错误")


def extract_json(text: str) -> dict:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        raise EngineError("format", "输出中没有 JSON：" + (text or "")[:200])
    try:
        return json.loads(t[s:e + 1])
    except json.JSONDecodeError as ex:
        raise EngineError("format", f"JSON 无法解析：{ex}")


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}


def _mask_email(e: str) -> str:
    return e or ""


def readable_error(text: str) -> str:
    """CLI 出错时常把调用栈打在前面，挑出真正的错误行。"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for l in lines:
        if re.search(r"(^|\b)(Error|error)[:：]|Missing optional dependency|not logged in|Not logged in", l):
            return l[:200]
    return lines[0][:200] if lines else ""


# ============================================================ 基类

class Engine:
    key = ""
    label = ""
    bin_name = ""
    cred_env: list[str] = []
    model_hints: list[str] = []

    def __init__(self):
        self.login_proc: subprocess.Popen | None = None
        self.login_output: list[str] = []
        self.login_started = 0.0

    # -- 环境
    def allow_env(self) -> bool:
        return bool(db.get_setting(f"{self.key}.allow_env_credentials", False))

    def env(self) -> dict:
        e = dict(os.environ)
        if not self.allow_env():
            for k in self.cred_env:
                e.pop(k, None)
        e["NO_COLOR"] = "1"
        return e

    def env_findings(self) -> list[str]:
        return [k for k in self.cred_env if os.environ.get(k)]

    def bin(self):
        return find_bin(self.bin_name)

    def run_cli(self, args, timeout=30, stdin=None) -> subprocess.CompletedProcess:
        exe = self.bin()
        if not exe:
            raise EngineError("unknown", f"未找到 {self.bin_name} 命令")
        with tempfile.TemporaryDirectory(prefix="novel_cli_") as tmp:
            return subprocess.run([exe, *args], cwd=tmp, env=self.env(), input=stdin, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def version(self) -> str:
        try:
            r = self.run_cli(["--version"], timeout=20)
            if r.returncode != 0:
                return "无法运行：" + readable_error(r.stderr + "\n" + r.stdout)
            return (r.stdout or r.stderr).strip().splitlines()[0][:80]
        except Exception:  # noqa: BLE001
            return ""

    # -- 登录（官方流程：调用 CLI 自己的登录命令，由它打开浏览器）
    def login_args(self) -> list[str]:
        raise NotImplementedError

    def start_login(self) -> dict:
        exe = self.bin()
        if not exe:
            raise EngineError("unknown", f"未找到 {self.bin_name} 命令")
        if self.login_proc and self.login_proc.poll() is None:
            return self.login_state()
        self.login_output = []
        self.login_started = time.time()
        tmp = tempfile.mkdtemp(prefix="novel_login_")
        self.login_proc = subprocess.Popen([exe, *self.login_args()], cwd=tmp, env=self.env(),
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", errors="replace")

        def pump(p=self.login_proc):
            for line in p.stdout:
                line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line).rstrip()
                if line:
                    self.login_output.append(line[:300])
                    self.login_output = self.login_output[-30:]
        threading.Thread(target=pump, daemon=True).start()

        def reaper(p=self.login_proc):
            try:
                p.wait(timeout=600)
            except subprocess.TimeoutExpired:
                p.kill()
        threading.Thread(target=reaper, daemon=True).start()
        return self.login_state()

    def login_state(self) -> dict:
        p = self.login_proc
        if not p:
            return {"running": False}
        urls = [u for line in self.login_output for u in re.findall(r"https?://\S+", line)]
        return {"running": p.poll() is None, "exit": p.poll(), "urls": urls[-2:],
                "output": [l for l in self.login_output if "token" not in l.lower()][-8:]}

    def cancel_login(self):
        if self.login_proc and self.login_proc.poll() is None:
            self.login_proc.kill()

    def logout_args(self) -> list[str]:
        raise NotImplementedError

    def logout(self):
        r = self.run_cli(self.logout_args(), timeout=30)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()[-300:]}

    # -- 状态
    def status(self) -> dict:
        raise NotImplementedError

    def info(self, deep=True) -> dict:
        exe = self.bin()
        base = {"key": self.key, "label": self.label, "installed": bool(exe), "path": exe or "",
                "allow_env": self.allow_env(), "env_findings": self.env_findings(),
                "model_hints": self.model_hints, "quota": "额度信息暂不可用",
                "last_verify": db.get_setting(f"{self.key}.last_verify"),
                "login": self.login_state()}
        if not exe:
            base.update({"logged_in": False, "status_text": f"未检测到 {self.bin_name} 命令"})
            return base
        if deep:
            base["version"] = self.version()
            try:
                base.update(self.status())
            except Exception as e:  # noqa: BLE001
                base.update({"logged_in": False, "status_text": f"无法读取登录状态：{e}"})
        return base

    # -- 调用
    def call(self, prompt: str, schema: dict, model: str, register, timeout: int = 1200) -> tuple[dict, str, dict]:
        raise NotImplementedError

    def verify(self, model: str) -> dict:
        schema = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
                  "additionalProperties": False}
        t0 = time.time()
        try:
            data, raw, usage = self.call("这是连接测试。请把 reply 字段设为 OK。", schema, model, lambda p: None, timeout=180)
            res = {"ok": True, "reply": str(data.get("reply", ""))[:40], "model": usage.get("model") or model or "CLI 默认",
                   "seconds": round(time.time() - t0, 1), "at": time.time()}
        except EngineError as e:
            res = {"ok": False, "kind": e.kind, "friendly": FRIENDLY.get(e.kind, ""), "error": e.message[-300:],
                   "seconds": round(time.time() - t0, 1), "at": time.time()}
        db.set_setting(f"{self.key}.last_verify", res)
        return res

    def _spawn(self, cmd, prompt, register, timeout, cwd):
        p = subprocess.Popen(cmd, cwd=cwd, env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             start_new_session=True)
        register(p)
        try:
            out, err = p.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            interrupt(p)
            p.communicate()
            raise EngineError("timeout", f"调用超过 {timeout} 秒未返回")
        finally:
            register(None)
        if getattr(p, "_cancelled", False):
            raise EngineError("cancelled", "已取消")
        return p.returncode, out or "", err or ""


def interrupt(p: subprocess.Popen):
    """官方中断方式：先发 SIGINT，5 秒不退出再强制结束。"""
    if not p or p.poll() is not None:
        return
    p._cancelled = True  # type: ignore[attr-defined]
    try:
        os.killpg(p.pid, signal.SIGINT)
    except Exception:  # noqa: BLE001
        p.send_signal(signal.SIGINT)
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            p.kill()


# ============================================================ Claude Code

class ClaudeEngine(Engine):
    key = "claude"
    label = "Claude Code"
    bin_name = "claude"
    cred_env = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY", "AWS_BEARER_TOKEN_BEDROCK"]
    model_hints = ["sonnet", "opus", "haiku"]

    def login_args(self):
        return ["auth", "login", "--claudeai"]

    def logout_args(self):
        return ["auth", "logout"]

    def config_findings(self) -> list[str]:
        out = []
        p = HOME / ".claude" / "settings.json"
        try:
            s = json.loads(p.read_text("utf-8"))
            if s.get("apiKeyHelper"):
                out.append("~/.claude/settings.json 配置了 apiKeyHelper（密钥脚本）")
            envs = [k for k in (s.get("env") or {}) if k in self.cred_env]
            if envs:
                out.append("~/.claude/settings.json 的 env 中设置了 " + "、".join(envs))
        except Exception:  # noqa: BLE001
            pass
        return out

    def status(self) -> dict:
        r = self.run_cli(["auth", "status", "--json"], timeout=30)
        try:
            st = json.loads(r.stdout)
        except Exception:  # noqa: BLE001
            st = {}
        logged = bool(st.get("loggedIn"))
        txt = ""
        try:
            txt = self.run_cli(["auth", "status", "--text"], timeout=30).stdout
        except Exception:  # noqa: BLE001
            pass
        fields = dict(re.findall(r"^([A-Za-z ]+):\s*(.+)$", txt, re.M))
        method = fields.get("Login method") or st.get("authMethod") or ""
        email = st.get("email") or st.get("emailAddress") or fields.get("Email") or ""
        org = st.get("orgName") or st.get("organizationName") or fields.get("Organization") or ""
        base_url = fields.get("Anthropic base URL", "")
        if not email or not org:
            try:
                acct = json.loads((HOME / ".claude.json").read_text("utf-8")).get("oauthAccount") or {}
                email = email or acct.get("emailAddress", "")
                org = org or acct.get("organizationName", "")
            except Exception:  # noqa: BLE001
                pass
        m = method.lower()
        if any(k in m for k in ("pro", "max")):
            kind = "个人订阅"
        elif any(k in m for k in ("team", "enterprise")):
            kind = "组织 / 团队订阅"
        elif "api" in m or "console" in m or st.get("authMethod") in ("api_key", "apiKey"):
            kind = "API 账户（按量计费）"
        else:
            kind = ""
        warnings = []
        if base_url and "api.anthropic.com" not in base_url:
            warnings.append(f"当前使用自定义网关：{base_url}")
        if st.get("apiProvider") and st.get("apiProvider") != "firstParty":
            warnings.append(f"当前服务提供方为 {st.get('apiProvider')}，不是 Anthropic 官方")
        if kind.startswith("API"):
            warnings.append("当前是 API 账户登录，不是个人订阅")
        warnings += self.config_findings()
        return {
            "logged_in": logged,
            "status_text": "已登录" if logged else "未登录",
            "account": {"email": _mask_email(email), "org": org, "method": method, "kind": kind or "请核对工作区",
                        "provider": st.get("apiProvider", "")},
            "warnings": warnings,
        }

    def call(self, prompt, schema, model, register, timeout=1200):
        exe = self.bin()
        if not exe:
            raise EngineError("unknown", "未找到 claude 命令")
        cmd = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--json-schema", json.dumps(schema, ensure_ascii=False)]
        if model:
            cmd += ["--model", model]
        with tempfile.TemporaryDirectory(prefix="novel_call_") as tmp:
            code, out, err = self._spawn(cmd, prompt, register, timeout, tmp)
        obj = None
        for line in reversed(out.strip().splitlines()):
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    break
                except Exception:  # noqa: BLE001
                    pass
        if obj is None:
            try:
                obj = json.loads(out)
            except Exception:  # noqa: BLE001
                raise classify(err + "\n" + out)
        if obj.get("is_error"):
            raise classify(str(obj.get("result", "")) + " " + str(obj.get("terminal_reason", "")),
                           obj.get("api_error_status"))
        usage = obj.get("usage") or {}
        mu = obj.get("modelUsage") or {}
        # Claude Code 会顺带用小模型做辅助任务，主模型取输出量最大的那个
        models = sorted(mu, key=lambda k: (mu[k] or {}).get("outputTokens", 0), reverse=True)
        u = {"input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
             + (usage.get("cache_creation_input_tokens") or 0),
             "output_tokens": usage.get("output_tokens") or 0, "model": models[0] if models else (model or "")}
        raw = obj.get("result") or ""
        data = obj.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(raw)
        return data, raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False), u


# ============================================================ Codex

class CodexEngine(Engine):
    key = "codex"
    label = "Codex"
    bin_name = "codex"
    cred_env = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
                "CODEX_API_KEY", "AZURE_OPENAI_API_KEY"]
    model_hints = []

    def codex_home(self) -> Path:
        return Path(os.environ.get("CODEX_HOME", HOME / ".codex"))

    def login_args(self):
        return ["login"]

    def logout_args(self):
        return ["logout"]

    def config(self) -> dict:
        out = {"model": "", "provider": "", "custom_base_url": False}
        try:
            txt = (self.codex_home() / "config.toml").read_text("utf-8")
        except Exception:  # noqa: BLE001
            return out
        top = txt.split("\n[", 1)[0]
        m = re.search(r'^\s*model\s*=\s*"([^"]+)"', top, re.M)
        if m:
            out["model"] = m.group(1)
        m = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', top, re.M)
        if m:
            out["provider"] = m.group(1)
        out["custom_base_url"] = bool(re.search(r"^\s*base_url\s*=", txt, re.M))
        return out

    def status(self) -> dict:
        r = self.run_cli(["login", "status"], timeout=30)
        text = (r.stdout + r.stderr).strip()
        logged = r.returncode == 0 and "not logged in" not in text.lower()
        email = plan = account = ""
        mode = ""
        try:
            auth = json.loads((self.codex_home() / "auth.json").read_text("utf-8"))
            mode = auth.get("auth_mode") or ("apikey" if auth.get("OPENAI_API_KEY") else "")
            tok = (auth.get("tokens") or {}).get("id_token") or ""
            c = _jwt_claims(tok) if isinstance(tok, str) else {}
            email = c.get("email", "")
            a = c.get("https://api.openai.com/auth") or {}
            plan = a.get("chatgpt_plan_type", "")
            account = a.get("chatgpt_account_id", "")
        except Exception:  # noqa: BLE001
            pass
        tl = text.lower()
        if "api key" in tl or mode.lower() in ("apikey", "api_key"):
            kind = "API Key（按量计费）"
        elif plan in ("plus", "pro", "free", "go"):
            kind = f"个人订阅（{plan}）"
        elif plan in ("team", "business", "enterprise", "edu"):
            kind = f"组织 / 团队订阅（{plan}）"
        else:
            kind = "请核对工作区"
        cfg = self.config()
        warnings = []
        if kind.startswith("API Key"):
            warnings.append("当前通过 API Key 登录，不是个人订阅")
        if cfg["provider"] and cfg["provider"] != "openai":
            warnings.append(f"config.toml 指定了模型提供方：{cfg['provider']}")
        if cfg["custom_base_url"]:
            warnings.append("config.toml 中配置了自定义 base_url（网关）")
        return {
            "logged_in": logged,
            "status_text": (readable_error(text) if r.returncode != 0 else text.splitlines()[0][:120]) if text
                           else ("已登录" if logged else "未登录"),
            "account": {"email": _mask_email(email), "org": account[:8] + "…" if account else "",
                        "method": text.splitlines()[0][:80] if (text and r.returncode == 0) else "",
                        "kind": kind, "provider": cfg["provider"]},
            "default_model": cfg["model"],
            "warnings": warnings,
        }

    def call(self, prompt, schema, model, register, timeout=1200):
        exe = self.bin()
        if not exe:
            raise EngineError("unknown", "未找到 codex 命令")
        with tempfile.TemporaryDirectory(prefix="novel_call_") as tmp:
            sp, op = Path(tmp) / "schema.json", Path(tmp) / "last.txt"
            sp.write_text(json.dumps(schema, ensure_ascii=False), "utf-8")
            cmd = [exe, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                   "--ignore-rules", "--output-schema", str(sp), "-o", str(op)]
            if model:
                cmd += ["-m", model]
            cmd.append("-")
            code, out, err = self._spawn(cmd, prompt, register, timeout, tmp)
            last = op.read_text("utf-8", errors="replace") if op.exists() else ""
        errors, usage, failed = [], {}, None
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            t = ev.get("type")
            if t == "error":
                errors.append(str(ev.get("message", "")))
            elif t == "turn.failed":
                failed = str((ev.get("error") or {}).get("message", "")) or "turn failed"
            elif t == "turn.completed":
                usage = ev.get("usage") or {}
        if failed or (code != 0 and not last.strip()):
            raise classify("\n".join([failed or "", *errors[-3:], err[-500:]]))
        u = {"input_tokens": usage.get("input_tokens") or 0, "output_tokens": usage.get("output_tokens") or 0,
             "model": model or self.config().get("model", "")}
        return extract_json(last), last, u


ENGINES: dict[str, Engine] = {"claude": ClaudeEngine(), "codex": CodexEngine()}


def get(key: str) -> Engine:
    if key not in ENGINES:
        raise ValueError(f"未知引擎：{key}")
    return ENGINES[key]
