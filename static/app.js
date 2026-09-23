function app() {
  return {
    version: "",
    healthOk: false,
    needAdmin: false,
    adminKey: localStorage.getItem("gg_admin_key") || "",
    adminError: "",
    serverCfg: {
      host: "127.0.0.1",
      port: 8789,
      local_api_key: "",
      admin_api_key: "",
      upstream_base_url: "https://daily-cloudcode-pa.googleapis.com",
      project: "aicode-consumers",
      default_model: "gemini-3.8-flash-high",
      strip_base_persona: true,
    },
    models: [],
    antigravity: {
      logged_in: false,
      email: "",
      expired: true,
      error: null,
    },
    weeklyQuota: null,
    fiveHourQuota: null,
    quotaGroups: [],
    tier: null,
    tab: "play",
    playMode: "chat",
    playModel: "gemini-3.8-flash-high",
    playPrompt: "你好！请简单介绍一下你自己。",
    playReply: "",
    playReasoning: "",
    playLatency: 0,
    playing: false,
    logs: [],
    logTotal: 0,
    testing: false,
    refreshing: false,
    syncingModels: false,

    get gatewayUrl() {
      const port = this.serverCfg.port || 8789;
      return `http://127.0.0.1:${port}/v1`;
    },

    async init() {
      await this.loadConfig();
      await this.fetchStatus();
      if (this.models.length > 0 && !this.playModel) {
        this.playModel = this.serverCfg.default_model || this.models[0].id;
      }
      setInterval(() => {
        if (!this.needAdmin) {
          this.fetchStatus();
        }
      }, 10000);
    },

    async loadConfig() {
      try {
        const resp = await fetch("/api/config", { headers: this._headers() });
        if (resp.ok) {
          const data = await resp.json();
          if (data.server) {
            this.serverCfg = { ...this.serverCfg, ...data.server };
          }
        }
      } catch (e) {}
    },

    _headers() {
      const h = { "Content-Type": "application/json" };
      if (this.adminKey) {
        h["Authorization"] = "Bearer " + this.adminKey;
        h["X-Admin-Key"] = this.adminKey;
      }
      return h;
    },

    async fetchStatus() {
      try {
        const resp = await fetch("/api/status", { headers: this._headers() });
        if (resp.status === 401 || resp.status === 403) {
          this.needAdmin = true;
          this.healthOk = false;
          return;
        }
        if (!resp.ok) {
          this.healthOk = false;
          return;
        }
        this.needAdmin = false;
        this.healthOk = true;
        const data = await resp.json();
        this.version = data.version || "";
        this.serverCfg.host = data.host;
        this.serverCfg.port = data.port;
        this.serverCfg.default_model = data.default_model;
        this.models = data.models || [];
        if (!this.playModel && this.serverCfg.default_model) {
          this.playModel = this.serverCfg.default_model;
        }
        this.antigravity = data.antigravity || {};
        if (data.quota && data.quota.quota) {
          this.quotaGroups = data.quota.quota.groups || [];
          this.weeklyQuota = data.quota.quota.weekly;
          this.fiveHourQuota = data.quota.quota.five_hour;
        }
        if (data.quota && data.quota.tier) {
          this.tier = data.quota.tier;
        }
        if (data.upstream) {
          if (!this.serverCfg.upstream_base_url && data.upstream.base_url) {
            this.serverCfg.upstream_base_url = data.upstream.base_url;
          }
          if (!this.serverCfg.project && data.upstream.project) {
            this.serverCfg.project = data.upstream.project;
          }
        }
      } catch (e) {
        this.healthOk = false;
      }
    },

    async unlockAdmin() {
      this.adminError = "";
      localStorage.setItem("gg_admin_key", this.adminKey);
      try {
        const resp = await fetch("/api/status", { headers: this._headers() });
        if (resp.status === 401 || resp.status === 403) {
          this.adminError = "密钥验证失败，请检查输入的密钥（默认 local_api_key 为 sk-local）";
          this.needAdmin = true;
          return;
        }
        this.needAdmin = false;
        this.adminError = "";
        await this.loadConfig();
        await this.fetchStatus();
      } catch (e) {
        this.adminError = "连接失败: " + e;
      }
    },

    async refreshAuth() {
      this.refreshing = true;
      try {
        const resp = await fetch("/api/auth/refresh", { method: "POST", headers: this._headers() });
        if (resp.ok) {
          await this.fetchStatus();
        } else {
          alert("凭据刷新失败: " + (await resp.text()));
        }
      } catch (e) {
        alert("网络错误: " + e);
      } finally {
        this.refreshing = false;
      }
    },

    async testConn() {
      this.testing = true;
      try {
        const resp = await fetch("/api/test", {
          method: "POST",
          headers: this._headers(),
          body: JSON.stringify({ model: this.playModel || this.serverCfg.default_model }),
        });
        const data = await resp.json();
        if (data.ok) {
          alert(`连通成功！模型: ${data.model}\n耗时: ${data.latency_ms} ms\n响应: ${data.reply}`);
        } else {
          alert("连通测试失败:\n" + (data.error || JSON.stringify(data)));
        }
      } catch (e) {
        alert("连通测试出错: " + e);
      } finally {
        this.testing = false;
      }
    },

    async syncModels() {
      this.syncingModels = true;
      try {
        const resp = await fetch("/api/models/fetch", { method: "POST", headers: this._headers() });
        if (resp.ok) {
          const data = await resp.json();
          alert(`成功同步 ${data.models.length} 个模型！`);
          await this.fetchStatus();
        } else {
          alert("同步模型失败: " + (await resp.text()));
        }
      } catch (e) {
        alert("同步出错: " + e);
      } finally {
        this.syncingModels = false;
      }
    },

    async fetchLogs() {
      try {
        const resp = await fetch("/api/logs?limit=100", { headers: this._headers() });
        if (resp.ok) {
          const data = await resp.json();
          this.logs = data.items || [];
          this.logTotal = data.total || 0;
        }
      } catch (e) {}
    },

    async clearLogs() {
      if (!confirm("确定清空请求日志吗？")) return;
      try {
        await fetch("/api/logs", { method: "DELETE", headers: this._headers() });
        this.logs = [];
        this.logTotal = 0;
      } catch (e) {}
    },

    async saveConfig() {
      try {
        const srv = { ...this.serverCfg };
        if (!srv.upstream_base_url) {
          srv.upstream_base_url = "https://daily-cloudcode-pa.googleapis.com";
        }
        if (!(srv.admin_api_key || "").trim()) {
          delete srv.admin_api_key;
        }
        const resp = await fetch("/api/config", {
          method: "PUT",
          headers: this._headers(),
          body: JSON.stringify({ server: srv }),
        });
        if (resp.ok) {
          alert("设置已保存");
          await this.loadConfig();
          await this.fetchStatus();
        } else {
          let errText = "";
          try {
            const errJson = await resp.json();
            errText = errJson.detail || JSON.stringify(errJson);
          } catch (_) {
            errText = await resp.text();
          }
          alert("保存失败: " + errText);
        }
      } catch (e) {
        alert("网络错误: " + e);
      }
    },

    async runPlay() {
      if (!this.playPrompt.trim()) return;
      this.playing = true;
      this.playReply = "";
      this.playReasoning = "";
      const t0 = Date.now();

      let body = {};
      if (this.playMode === "chat") {
        body = {
          model: this.playModel,
          messages: [{ role: "user", content: this.playPrompt }],
        };
      } else if (this.playMode === "messages") {
        body = {
          model: this.playModel,
          messages: [{ role: "user", content: this.playPrompt }],
          max_tokens: 1024,
        };
      } else if (this.playMode === "responses") {
        body = {
          model: this.playModel,
          input: [{ role: "user", content: this.playPrompt }],
        };
      }

      try {
        const resp = await fetch(`/api/play/${this.playMode}`, {
          method: "POST",
          headers: this._headers(),
          body: JSON.stringify(body),
        });
        this.playLatency = Date.now() - t0;
        const resJson = await resp.json();

        if (this.playMode === "chat") {
          const choice = (resJson.choices || [])[0] || {};
          const msg = choice.message || {};
          this.playReply = msg.content || "";
          this.playReasoning = msg.reasoning_content || "";
        } else if (this.playMode === "messages") {
          const contents = resJson.content || [];
          let reply = "";
          let reasoning = "";
          for (const c of contents) {
            if (c.type === "text") reply += c.text;
            if (c.type === "thinking") reasoning += c.thinking;
          }
          this.playReply = reply;
          this.playReasoning = reasoning;
        } else if (this.playMode === "responses") {
          this.playReply = resJson.output_text || JSON.stringify(resJson.output || resJson, null, 2);
        }
      } catch (e) {
        this.playReply = "请求异常: " + e;
      } finally {
        this.playing = false;
      }
    },

    fmtReset(iso) {
      if (!iso) return "—";
      try {
        const d = new Date(iso);
        return d.toLocaleString();
      } catch (e) {
        return iso;
      }
    },

    fmtTime(ts) {
      if (!ts) return "—";
      try {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString();
      } catch (e) {
        return "" + ts;
      }
    },

    copy(text) {
      navigator.clipboard.writeText(text);
      alert("已复制到剪贴板: " + text);
    },
  };
}
