// Environment-backed console settings.
import {
  CheckCircle2,
  Eye,
  EyeOff,
  HardDrive,
  LoaderCircle,
  Save,
  ShieldCheck,
  Wifi
} from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../shared/api/client";
import { Field, PageHeader, SettingsGroup } from "../../shared/components/ui";

type TestState = "idle" | "testing" | "success" | "failed";

export function SettingsPage() {
  const [showKey, setShowKey] = useState(false);
  const [llmTest, setLlmTest] = useState<TestState>("idle");
  const [embeddingTest, setEmbeddingTest] = useState<TestState>("idle");
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [llm, setLlm] = useState({
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
    provider: "OpenAI Compatible",
    apiKey: "••••••••••••••••",
    temperature: "0.2",
    timeout: "60"
  });
  const [embedding, setEmbedding] = useState({
    model: "BAAI/bge-small-zh-v1.5",
    dimensions: "512",
    runner: "FastEmbed ONNX / CPU",
    source: "HuggingFace Hub",
    cachePath: "./models/fastembed",
    offline: false
  });
  const settings = useQuery({
    queryKey: ["environment-settings"],
    queryFn: api.getEnvironmentSettings
  });

  useEffect(() => {
    const values = settings.data?.values;
    if (!values) return;
    setLlm((current) => ({
      ...current,
      baseUrl: values.LLM_BASE_URL || current.baseUrl,
      model: values.LLM_MODEL || current.model,
      provider: values.LLM_PROVIDER || current.provider,
      apiKey: values.LLM_API_KEY || current.apiKey,
      temperature: values.LLM_TEMPERATURE || current.temperature,
      timeout: values.LLM_TIMEOUT || current.timeout
    }));
    setEmbedding((current) => ({
      ...current,
      model: values.EMBEDDING_MODEL || current.model,
      dimensions: values.EMBEDDING_DIMENSION || current.dimensions,
      cachePath: values.FASTEMBED_CACHE_PATH || values.EMBEDDING_CACHE_PATH || current.cachePath,
      source: values.MODEL_SOURCE || current.source,
      offline: values.HF_HUB_OFFLINE === "1" || values.EMBEDDING_OFFLINE === "1"
    }));
  }, [settings.data]);

  const saveSettings = useMutation({
    mutationFn: () =>
      api.saveEnvironmentSettings({
        LLM_BASE_URL: llm.baseUrl,
        LLM_MODEL: llm.model,
        LLM_PROVIDER: llm.provider,
        LLM_API_KEY: llm.apiKey,
        LLM_TEMPERATURE: llm.temperature,
        LLM_TIMEOUT: llm.timeout,
        EMBEDDING_MODEL: embedding.model,
        EMBEDDING_DIMENSION: embedding.dimensions,
        EMBEDDING_CACHE_PATH: embedding.cachePath,
        FASTEMBED_CACHE_PATH: embedding.cachePath,
        MODEL_SOURCE: embedding.source,
        EMBEDDING_OFFLINE: embedding.offline ? "1" : "0",
        HF_HUB_OFFLINE: embedding.offline ? "1" : "0"
      }),
    onSuccess: () => {
      setSaveError(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 3200);
    },
    onError: (cause) => {
      setSaved(false);
      setSaveError(cause instanceof Error ? cause.message : "设置保存失败");
    }
  });

  async function test(type: "llm" | "embedding") {
    const setState = type === "llm" ? setLlmTest : setEmbeddingTest;
    setState("testing");
    try {
      const result = await api.testSettings(type);
      setState(result.status === "ok" ? "success" : "failed");
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="page-stack settings-page">
      <PageHeader
        title="设置"
        description="配置项通过服务端安全读写。密钥只显示掩码；保存后如需重启服务，会在此处明确提示。"
        actions={
          <button
            className="button button-primary"
            type="button"
            disabled={saveSettings.isPending || settings.isLoading}
            onClick={() => saveSettings.mutate()}
          >
            {saveSettings.isPending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
            {saveSettings.isPending ? "保存中…" : "保存设置"}
          </button>
        }
      />

      {saved ? (
        <div className="page-notice success" role="status">
          <CheckCircle2 size={15} />
          设置已原子写入白名单环境变量。请重启服务使配置生效。
        </div>
      ) : null}
      {saveError ? <div className="inline-error">{saveError}</div> : null}

      <SettingsGroup
        title="LLM · OpenAI 兼容接口"
        description="用于诊断推理、会话续谈和结构化结论生成。"
        action={
          <button
            className="button button-quiet button-sm"
            type="button"
            disabled={llmTest === "testing"}
            onClick={() => test("llm")}
          >
            {llmTest === "testing" ? <LoaderCircle className="spin" size={14} /> : <Wifi size={14} />}
            {llmTest === "testing" ? "测试中…" : llmTest === "success" ? "连接正常" : "测试连接"}
          </button>
        }
      >
        <Field label="Base URL">
          <input className="field mono" value={llm.baseUrl} onChange={(event) => setLlm({ ...llm, baseUrl: event.target.value })} />
        </Field>
        <Field label="模型">
          <input className="field" value={llm.model} onChange={(event) => setLlm({ ...llm, model: event.target.value })} />
        </Field>
        <Field label="Provider">
          <input className="field" value={llm.provider} onChange={(event) => setLlm({ ...llm, provider: event.target.value })} />
        </Field>
        <Field label="API Key" hint="始终以掩码返回，不提供明文回读接口">
          <div className="password-field">
            <input
              className="field mono"
              type={showKey ? "text" : "password"}
              value={llm.apiKey}
              onChange={(event) => setLlm({ ...llm, apiKey: event.target.value })}
            />
            <button className="icon-button" type="button" aria-label="切换密钥显示" onClick={() => setShowKey((value) => !value)}>
              {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
        </Field>
        <Field label="Temperature">
          <input className="field" value={llm.temperature} onChange={(event) => setLlm({ ...llm, temperature: event.target.value })} />
        </Field>
        <Field label="请求超时（秒）">
          <input className="field" value={llm.timeout} onChange={(event) => setLlm({ ...llm, timeout: event.target.value })} />
        </Field>
      </SettingsGroup>

      <SettingsGroup
        title="Embedding · 本地向量模型"
        description="向量模型必须与索引构建使用同一模型和维度。"
        action={
          <button
            className="button button-quiet button-sm"
            type="button"
            disabled={embeddingTest === "testing"}
            onClick={() => test("embedding")}
          >
            {embeddingTest === "testing" ? <LoaderCircle className="spin" size={14} /> : <HardDrive size={14} />}
            {embeddingTest === "testing" ? "加载中…" : embeddingTest === "success" ? "模型可用" : "测试加载"}
          </button>
        }
      >
        <Field label="模型名">
          <input className="field" value={embedding.model} onChange={(event) => setEmbedding({ ...embedding, model: event.target.value })} />
        </Field>
        <Field label="向量维度">
          <input className="field" value={embedding.dimensions} onChange={(event) => setEmbedding({ ...embedding, dimensions: event.target.value })} />
        </Field>
        <Field label="运行方式">
          <input className="field" value={embedding.runner} onChange={(event) => setEmbedding({ ...embedding, runner: event.target.value })} />
        </Field>
        <Field label="模型获取地点">
          <select className="field" value={embedding.source} onChange={(event) => setEmbedding({ ...embedding, source: event.target.value })}>
            <option>HuggingFace Hub</option>
            <option>ModelScope</option>
            <option>本地目录</option>
            <option>自定义镜像 / 代理</option>
          </select>
        </Field>
        <Field label="缓存路径">
          <input className="field mono" value={embedding.cachePath} onChange={(event) => setEmbedding({ ...embedding, cachePath: event.target.value })} />
        </Field>
        <Field label="离线模式">
          <label className="switch-row">
            <input
              type="checkbox"
              checked={embedding.offline}
              onChange={(event) => setEmbedding({ ...embedding, offline: event.target.checked })}
            />
            <span>{embedding.offline ? "仅使用本地缓存" : "允许按需获取模型"}</span>
          </label>
        </Field>
      </SettingsGroup>

      <section className="panel env-preview-panel">
        <header className="panel-head">
          <div>
            <h2 className="panel-title">
              <ShieldCheck size={15} />
              环境变量预览
            </h2>
            <p className="panel-subtitle">仅可修改白名单配置。保存前会创建备份，并校验所有敏感字段。</p>
          </div>
          <span className="tag success">API Key 已掩码</span>
        </header>
        <pre>{`LLM_BASE_URL=${llm.baseUrl}
LLM_MODEL=${llm.model}
LLM_API_KEY=••••••••••••••••
LLM_TEMPERATURE=${llm.temperature}

EMBEDDING_MODEL=${embedding.model}
FASTEMBED_CACHE_PATH=${embedding.cachePath}
HF_HUB_OFFLINE=${embedding.offline ? "1" : "0"}`}</pre>
      </section>
    </div>
  );
}
