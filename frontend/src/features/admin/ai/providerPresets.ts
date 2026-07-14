/** Provider connection presets — how a company plugs an AI source in.
 *
 * A preset is UX packaging over the domain triplet (kind, adapter, base_url):
 * the grouped picker in the "add provider" form. It carries no new domain rule
 * — kind/adapter are backend enums, and the default base URL is a placeholder
 * the admin may override. Cloud vendors that speak the OpenAI dialect
 * (DeepSeek, Qwen, aggregators, …) all ride `openai_compatible`; `kind` — not
 * the adapter — is what separates a Chinese *cloud* from a self-hosted vLLM.
 *
 * Adding a cloud preset here is pure frontend. Vendors with a non-OpenAI
 * dialect (Voyage, Cohere) would need a backend adapter first — deferred. */

/** UI grouping in the picker; each maps to one labelled optgroup. */
export type PresetGroup = "cloud" | "aggregator" | "chinese" | "selfHosted";

export interface ProviderPreset {
  id: string;
  group: PresetGroup;
  /** Vendor proper name — shown verbatim, not translated. */
  label: string;
  /** i18n key for descriptive presets ("Other…", the vLLM/TGI gateway); overrides
   * `label` when set. Typed to the known keys so t() stays strict (see
   * reference-i18next-ts2589). */
  labelKey?: "admin.aiModels.presets.otherCloud" | "admin.aiModels.presets.vllm";
  adapter: string;
  kind: "cloud" | "local";
  /** Prefilled default; the admin can edit it. Absent → the field starts empty. */
  baseUrl?: string;
  /** Placeholder when there is no default (self-hosted, generic gateways). */
  placeholder?: string;
}

/** Native SDK adapters carry a built-in endpoint, so the form hides base URL;
 * everything else (openai_compatible, ollama) must point somewhere. */
const NATIVE_ADAPTERS = new Set(["openai", "anthropic", "google"]);

export const PROVIDER_PRESETS: ProviderPreset[] = [
  // Cloud vendors — native dialects + frontier labs on the OpenAI dialect.
  { id: "openai", group: "cloud", label: "OpenAI", adapter: "openai", kind: "cloud" },
  { id: "anthropic", group: "cloud", label: "Anthropic", adapter: "anthropic", kind: "cloud" },
  { id: "google", group: "cloud", label: "Google Gemini", adapter: "google", kind: "cloud" },
  {
    id: "grok",
    group: "cloud",
    label: "xAI Grok",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.x.ai/v1",
  },
  {
    id: "mistral",
    group: "cloud",
    label: "Mistral",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.mistral.ai/v1",
  },
  // Aggregators & fast inference — one key, many models. The generic
  // OpenAI-compatible entry leads: it is the catch-all for any gateway not
  // listed below (the common case when plugging a new aggregator in).
  {
    id: "other_cloud",
    group: "aggregator",
    labelKey: "admin.aiModels.presets.otherCloud",
    label: "OpenAI-compatible",
    adapter: "openai_compatible",
    kind: "cloud",
    placeholder: "https://…/v1",
  },
  {
    id: "openrouter",
    group: "aggregator",
    label: "OpenRouter",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://openrouter.ai/api/v1",
  },
  {
    id: "groq",
    group: "aggregator",
    label: "Groq",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.groq.com/openai/v1",
  },
  {
    id: "together",
    group: "aggregator",
    label: "Together AI",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.together.xyz/v1",
  },
  {
    id: "fireworks",
    group: "aggregator",
    label: "Fireworks AI",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.fireworks.ai/inference/v1",
  },
  // Chinese clouds — all OpenAI-compatible, prefilled endpoints.
  {
    id: "deepseek",
    group: "chinese",
    label: "DeepSeek",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.deepseek.com",
  },
  {
    id: "qwen",
    group: "chinese",
    label: "Qwen (Alibaba)",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
  },
  {
    id: "glm",
    group: "chinese",
    label: "GLM (Zhipu)",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
  },
  {
    id: "kimi",
    group: "chinese",
    label: "Kimi (Moonshot)",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.moonshot.cn/v1",
  },
  {
    id: "minimax",
    group: "chinese",
    label: "MiniMax",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.minimax.chat/v1",
  },
  {
    id: "ernie",
    group: "chinese",
    label: "Baidu ERNIE",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://qianfan.baidubce.com/v2",
  },
  {
    id: "yi",
    group: "chinese",
    label: "Yi (01.AI)",
    adapter: "openai_compatible",
    kind: "cloud",
    baseUrl: "https://api.lingyiwanwu.com/v1",
  },
  // Self-hosted — company's own runtime; base URL required, key optional.
  {
    id: "ollama",
    group: "selfHosted",
    label: "Ollama",
    adapter: "ollama",
    kind: "local",
    placeholder: "http://ollama:11434",
  },
  {
    id: "vllm",
    group: "selfHosted",
    labelKey: "admin.aiModels.presets.vllm",
    label: "OpenAI-compatible · vLLM · TGI",
    adapter: "openai_compatible",
    kind: "local",
    placeholder: "https://…/v1",
  },
];

/** Order of optgroups in the picker. */
export const PRESET_GROUP_ORDER: PresetGroup[] = ["cloud", "selfHosted", "aggregator", "chinese"];

/** A cloud vendor with a native SDK endpoint hides the base-URL field. */
export function presetHidesBaseUrl(preset: ProviderPreset): boolean {
  return NATIVE_ADAPTERS.has(preset.adapter);
}

/** Cloud providers require a key to reach the vendor; self-hosted is optional. */
export function presetRequiresKey(preset: ProviderPreset): boolean {
  return preset.kind === "cloud";
}
