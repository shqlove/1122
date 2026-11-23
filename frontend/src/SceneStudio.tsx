import { useEffect, useState } from "react";
import { Download, Plus, Save, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "./lib/api";

type Shot = {
  speaker: string;
  focus: string;
  text: string;
  action: string;
  seconds: number;
};
type Sheet = {
  id: string;
  revision: number;
  name: string;
  setting: string;
  a_name: string;
  b_name: string;
  a_look?: string;
  b_look?: string;
  ratio: string;
  limit: number;
  shots: Shot[];
  assets: Record<string, string>;
};
type Asset = {
  asset_id: string;
  name: string;
  kind: string;
  duration: number | null;
};
const slots = [
  ["a_image", "主角三视图", "image"],
  ["b_image", "配角三视图", "image"],
  ["scene_image", "统一场景图", "image"],
  ["a_audio", "主角音色", "audio"],
  ["b_audio", "配角音色", "audio"],
] as const;
const blankShot = (): Shot => ({
  speaker: "A",
  focus: "A",
  text: "",
  action: "",
  seconds: 0,
});
const blank = (): Sheet => ({
  id: "",
  revision: 0,
  name: "",
  setting: "",
  a_name: "",
  b_name: "",
  ratio: "4:3",
  limit: 30,
  shots: [blankShot()],
  assets: {},
});
let pending: Sheet | null = null;
let lastId = "";

function Reference({
  label,
  kind,
  value,
  assets,
  onPick,
  onImport,
  disabled,
}: {
  label: string;
  kind: string;
  value: string;
  assets: Asset[];
  onPick: (id: string) => void;
  onImport: () => void;
  disabled: boolean;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setUrl("");
    setError("");
    if (value)
      api("scene_preview", value)
        .then((v) => {
          if (active) setUrl(v);
        })
        .catch((e) => {
          if (active) setError(String(e.message || e));
        });
    return () => {
      active = false;
    };
  }, [value]);
  return (
    <div className="scene-reference">
      <strong>{label}</strong>
      {url &&
        (kind === "image" ? (
          <img src={url} alt={label} />
        ) : (
          <audio controls preload="metadata" src={url} aria-label={label} />
        ))}
      {error && <span role="alert">{error}</span>}
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onPick(e.target.value)}
      >
        <option value="">选择已导入素材</option>
        {assets
          .filter((a) => a.kind === kind)
          .map((a) => (
            <option key={a.asset_id} value={a.asset_id}>
              {a.name}
            </option>
          ))}
      </select>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={onImport}
      >
        <Upload size={14} />
        导入{kind === "image" ? "图片" : "音频"}
      </Button>
    </div>
  );
}

export function SceneStudio({ ready }: { ready: boolean }) {
  const [form, setForm] = useState<Sheet>(() => pending || blank());
  const [sheets, setSheets] = useState<Sheet[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [plan, setPlan] = useState<{ seconds: number; shots: Shot[] }[]>([]);
  const [planError, setPlanError] = useState("");
  const [dirty, setDirty] = useState(Boolean(pending));
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    api("scene_state")
      .then((s) => {
        if (!active) return;
        setSheets(s.sheets);
        setAssets(s.assets);
        setForm(
          pending ||
            s.sheets.find((v: Sheet) => v.id === lastId) ||
            s.sheets[0] ||
            blank(),
        );
        setLoaded(true);
      })
      .catch((e) => {
        if (active) setMessage(String(e.message || e));
      });
    return () => {
      active = false;
    };
  }, [ready, retry]);
  useEffect(() => {
    if (!loaded) return;
    let active = true;
    setPlan([]);
    setPlanError("");
    const timer = setTimeout(
      () =>
        api("scene_plan", form)
          .then((p) => {
            if (active) setPlan(p);
          })
          .catch((e) => {
            if (active) setPlanError(String(e.message || e));
          }),
      150,
    );
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [loaded, form.shots, form.limit]);
  function edit(changes: Partial<Sheet>) {
    const next = { ...form, ...changes };
    pending = next;
    setForm(next);
    setDirty(true);
    setMessage("");
  }
  function select(next: Sheet) {
    if (dirty && !window.confirm("放弃本制作单尚未保存的修改？")) return;
    pending = null;
    lastId = next.id;
    setDirty(false);
    setForm(next);
    setMessage("");
  }
  async function perform(run: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await run();
    } catch (e: any) {
      setMessage(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    const saved: Sheet = await api("scene_save", form);
    setSheets((prev) => [saved, ...prev.filter((s) => s.id !== saved.id)]);
    setForm(saved);
    setDirty(false);
    pending = null;
    lastId = saved.id;
    return saved;
  }
  function changeShot(index: number, changes: Partial<Shot>) {
    edit({
      shots: form.shots.map((s, i) => (i === index ? { ...s, ...changes } : s)),
    });
  }
  if (!loaded)
    return (
      <div className="notice">
        {message || "正在载入制作单…"}{" "}
        {message && (
          <Button
            onClick={() => {
              setMessage("");
              setRetry((r) => r + 1);
            }}
          >
            重新载入
          </Button>
        )}
      </div>
    );
  const complete = slots.every(([key]) => !!form.assets[key]);
  return (
    <fieldset disabled={busy} className="scene-studio">
      <div className="row wrap">
        <select
          aria-label="选择场景制作单"
          value={form.id}
          onChange={(e) =>
            select(sheets.find((s) => s.id === e.target.value) || blank())
          }
        >
          <option value="">新制作单</option>
          {sheets.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <Button variant="outline" onClick={() => select(blank())}>
          <Plus />
          新建
        </Button>
        <Button
          variant="outline"
          disabled={!form.name.trim()}
          onClick={() =>
            perform(async () => {
              await save();
              setMessage("制作单已保存");
            })
          }
        >
          <Save />
          保存制作单
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            if (
              dirty &&
              !window.confirm("放弃未保存的修改，重新载入本地制作单？")
            )
              return;
            pending = null;
            lastId = form.id;
            setDirty(false);
            setLoaded(false);
            setMessage("");
            setRetry((r) => r + 1);
          }}
        >
          重新载入
        </Button>
        {dirty && <span className="muted">有未保存的修改</span>}
      </div>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      <section>
        <h2>角色与场景</h2>
        <div className="scene-fields">
          <label>
            制作单名称
            <Input
              value={form.name}
              onChange={(e) => edit({ name: e.target.value })}
            />
          </label>
          <label>
            A · 主角
            <Input
              value={form.a_name}
              onChange={(e) => edit({ a_name: e.target.value })}
            />
          </label>
          <label>
            B · 配角
            <Input
              value={form.b_name}
              onChange={(e) => edit({ b_name: e.target.value })}
            />
          </label>
        </div>
        <label>
          场景设定
          <Textarea
            value={form.setting}
            placeholder="地点、年代、光线和人物站位"
            onChange={(e) => edit({ setting: e.target.value })}
          />
        </label>
        <details>
          <summary>准备生图提示词</summary>
          <div className="scene-fields">
            {(["a", "b"] as const).map((role) => (
              <label key={role}>
                {role === "a" ? "主角" : "配角"}主体正向词
                <Textarea
                  placeholder="外貌、发型、服装，或说明遵循哪张人物参考照片"
                  value={form[role + "_look"] || ""}
                  onChange={(e) => edit({ [role + "_look"]: e.target.value })}
                />
              </label>
            ))}
          </div>
          {(["a", "b"] as const).map((role) => (
            <label key={role}>
              {role === "a" ? "主角" : "配角"}三视图提示词
              <Textarea
                readOnly
                value={[
                  form[role + "_name"],
                  form[role + "_look"],
                  "16:9 白底真人设定图。展示同一人物的正脸特写、正面、左侧面、背面全身，以及中性、微笑、严肃、愤怒、惊讶、思考六种表情。统一五官、服装、妆发和光照。",
                ]
                  .filter(Boolean)
                  .join("。")}
              />
            </label>
          ))}
          <label>
            场景空镜提示词
            <Textarea
              readOnly
              value={[
                form.setting,
                "同一地点的无人场景参考图，交代前中后景道具、空间关系和光源方向。",
              ]
                .filter(Boolean)
                .join("。")}
            />
          </label>
        </details>
        <div className="scene-refs">
          {slots.map(([key, label, kind]) => (
            <Reference
              key={key}
              label={label}
              kind={kind}
              assets={assets}
              disabled={busy}
              value={form.assets[key] || ""}
              onPick={(id) => {
                const refs = { ...form.assets };
                if (id) refs[key] = id;
                else delete refs[key];
                edit({ assets: refs });
              }}
              onImport={() =>
                perform(async () => {
                  const a: Asset | null = await api("scene_choose_asset", key);
                  if (!a) return;
                  setAssets((prev) => [
                    ...prev.filter((v) => v.asset_id !== a.asset_id),
                    a,
                  ]);
                  edit({ assets: { ...form.assets, [key]: a.asset_id } });
                })
              }
            />
          ))}
        </div>
      </section>
      <section>
        <div className="row between wrap">
          <h2>分镜与台词</h2>
          <div className="row wrap">
            <label>
              画幅{" "}
              <select
                value={form.ratio}
                onChange={(e) => edit({ ratio: e.target.value })}
              >
                {["4:3", "9:16", "16:9"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              单段时长上限{" "}
              <select
                value={form.limit}
                onChange={(e) => edit({ limit: Number(e.target.value) })}
              >
                <option value={15}>15 秒</option>
                <option value={30}>30 秒</option>
              </select>
            </label>
          </div>
        </div>
        {form.shots.map((s, i) => (
          <div className="scene-shot" key={i}>
            <div className="row wrap">
              <b>镜头 {i + 1}</b>
              <label>
                说话人{" "}
                <select
                  value={s.speaker}
                  onChange={(e) => changeShot(i, { speaker: e.target.value })}
                >
                  <option>A</option>
                  <option>B</option>
                </select>
              </label>
              <label>
                画面拍谁{" "}
                <select
                  value={s.focus}
                  onChange={(e) => changeShot(i, { focus: e.target.value })}
                >
                  <option>A</option>
                  <option>B</option>
                  <option value="AB">双人同框</option>
                </select>
              </label>
              <label>
                镜头秒数{" "}
                <input
                  type="number"
                  min="0"
                  max="120"
                  aria-label={`镜头 ${i + 1} 秒数`}
                  value={s.seconds || ""}
                  placeholder="自动"
                  onChange={(e) =>
                    changeShot(i, { seconds: Number(e.target.value) })
                  }
                />
              </label>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`删除镜头 ${i + 1}`}
                onClick={() =>
                  edit({ shots: form.shots.filter((_, n) => n !== i) })
                }
              >
                <Trash2 size={15} />
              </Button>
            </div>
            <label>
              台词
              <Textarea
                value={s.text}
                onChange={(e) => changeShot(i, { text: e.target.value })}
              />
            </label>
            <label>
              动作与衔接
              <Input
                value={s.action}
                placeholder="例如 A 继续说，B 看向 A，保持闭口"
                onChange={(e) => changeShot(i, { action: e.target.value })}
              />
            </label>
          </div>
        ))}
        <Button
          variant="outline"
          onClick={() => edit({ shots: [...form.shots, blankShot()] })}
        >
          <Plus />
          添加镜头
        </Button>
      </section>
      <section>
        <h2>分段预览</h2>
        {planError ? (
          <p className="muted">{planError}</p>
        ) : (
          plan.map((p, i) => (
            <p key={i}>
              第 {i + 1} 段 · 镜头{" "}
              {p.shots.map((s: any) => s.number).join("、")} · 预计 {p.seconds}{" "}
              秒
            </p>
          ))
        )}
        <p className="muted">
          按每秒约 5
          字估时，可指定镜头秒数。导出包包含三图、两份音色、制作单和分段提示词。
        </p>
        <Button
          disabled={
            !complete ||
            !form.name.trim() ||
            !form.setting.trim() ||
            !form.a_name.trim() ||
            !form.b_name.trim() ||
            !plan.length
          }
          onClick={() =>
            perform(async () => {
              const saved = dirty || !form.id ? await save() : form;
              const path = await api("scene_export", saved);
              setMessage(
                path ? "制作包已导出至 " + path : "制作单已保存，已取消导出",
              );
            })
          }
        >
          <Download />
          导出制作包
        </Button>
      </section>
    </fieldset>
  );
}
