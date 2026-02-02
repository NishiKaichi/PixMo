import { useEffect, useMemo, useState } from "react";
import "./App.css";

const SESSION_KEY = "pixmo_session_id";
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

function apiUrl(path: string): string {
  if (!API_BASE) return path;
  const base = API_BASE.endsWith("/") ? API_BASE.slice(0, -1) : API_BASE;
  return `${base}${path}`;
}

function getOrCreateSessionId(): string {
  const existing = localStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const sid = crypto.randomUUID();
  localStorage.setItem(SESSION_KEY, sid);
  return sid;
}

type Material = {
  id: string;
  name: string;
  status: "queued" | "processing" | "ready" | "error";
  progress: number;
  message: string;
  count: number;
};

type Target = {
  id: string;
  name: string;
  path?: string;
  width: number;
  height: number;
};

type JobStatus = "queued" | "running" | "done" | "error";

export default function App() {
  const sessionId = useMemo(() => getOrCreateSessionId(), []);

  async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
    const headers = new Headers(init?.headers ?? {});
    headers.set("X-Session-Id", sessionId);
    if (typeof input === "string" && input.startsWith("/")) {
      return fetch(apiUrl(input), { ...init, headers });
    }
    return fetch(input, { ...init, headers });
  }

  const [materials, setMaterials] = useState<Material[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);

  const [selectedMaterial, setSelectedMaterial] = useState<string>("");
  const [selectedTarget, setSelectedTarget] = useState<string>("");

  const [matZip, setMatZip] = useState<File | null>(null);
  const [matName, setMatName] = useState<string>("materials");
  const [targetFile, setTargetFile] = useState<File | null>(null);

  const [tileSize, setTileSize] = useState<number>(64);
  const [noRepeatK, setNoRepeatK] = useState<number>(15);
  const [colorStrength, setColorStrength] = useState<number>(0.35);
  const [overlayStrength, setOverlayStrength] = useState<number>(0.2);
  const [overlayEnabled, setOverlayEnabled] = useState<boolean>(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState<number>(0);
  const [message, setMessage] = useState<string>("");

  const resultUrl = useMemo(() => {
    if (!jobId || status !== "done") return null;
    return apiUrl(`/api/jobs/${jobId}/result?sid=${encodeURIComponent(sessionId)}&v=${Date.now()}`);
  }, [jobId, status, sessionId]);

  async function refreshMaterials() {
    const res = await apiFetch("/api/materials");
    const data = await res.json();
    setMaterials(data.materials);
    if (!selectedMaterial && data.materials?.length) {
      const ready = data.materials.find((m: Material) => m.status === "ready") || data.materials[0];
      setSelectedMaterial(ready.id);
    }
  }

  async function refreshTargets() {
    const res = await apiFetch("/api/targets");
    const data = await res.json();
    setTargets(data.targets);
    if (!selectedTarget && data.targets?.length) {
      setSelectedTarget(data.targets[0].id);
    }
  }

  useEffect(() => {
    const onPageHide = () => {
      const payload = JSON.stringify({ session_id: sessionId });
      navigator.sendBeacon(
        apiUrl("/api/session/close"),
        new Blob([payload], { type: "application/json" })
      );
    };
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [sessionId]);

  useEffect(() => {
    refreshMaterials();
    refreshTargets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const hasProcessing = materials.some((m) => m.status === "queued" || m.status === "processing");
    if (!hasProcessing) return;

    const t = setInterval(() => {
      refreshMaterials();
    }, 1200);

    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [materials]);

  async function uploadMaterials() {
    if (!matZip) {
      alert("素材ZIPを選んでください");
      return;
    }
    const fd = new FormData();
    fd.append("tiles_zip", matZip);
    fd.append("name", matName);

    const res = await apiFetch("/api/materials", { method: "POST", body: fd });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshMaterials();
    setMatZip(null);
  }

  async function uploadTarget() {
    if (!targetFile) {
      alert("ターゲット画像を選んでください");
      return;
    }
    const fd = new FormData();
    fd.append("image", targetFile);

    const res = await apiFetch("/api/targets", { method: "POST", body: fd });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshTargets();
    setTargetFile(null);
  }

  async function deleteMaterial(id: string) {
    if (!confirm("この素材セットを削除しますか？")) return;
    const res = await apiFetch(`/api/materials/${id}`, { method: "DELETE" });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshMaterials();
    if (selectedMaterial === id) setSelectedMaterial("");
  }

  async function deleteTarget(id: string) {
    if (!confirm("このターゲット画像を削除しますか？")) return;
    const res = await apiFetch(`/api/targets/${id}`, { method: "DELETE" });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshTargets();
    if (selectedTarget === id) setSelectedTarget("");
  }

  async function startJob() {
    if (!selectedTarget || !selectedMaterial) {
      alert("ターゲットと素材セットを選択してください");
      return;
    }
    const mat = materials.find((m) => m.id === selectedMaterial);
    if (mat && mat.status !== "ready") {
      alert("素材セットが準備中です（processing/error）");
      return;
    }

    const fd = new FormData();
    fd.append("target_id", selectedTarget);
    fd.append("material_id", selectedMaterial);
    fd.append("tile_size", String(tileSize));
    fd.append("no_repeat_k", String(noRepeatK));
    fd.append("color_strength", String(colorStrength));
    fd.append("overlay_strength", String(overlayEnabled ? overlayStrength : 0));

    setMessage("Creating job...");
    const res = await apiFetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    const data = await res.json();
    setJobId(data.job_id);
    setStatus("queued");
    setProgress(0);
    setMessage("Queued");
  }

  useEffect(() => {
    if (!jobId) return;

    const timer = setInterval(async () => {
      const res = await apiFetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const data = await res.json();
      setStatus(data.status);
      setProgress(data.progress);
      setMessage(data.message);

      if (data.status === "done" || data.status === "error") {
        clearInterval(timer);
      }
    }, 800);

    return () => clearInterval(timer);
  }, [jobId]);

  const selectedTargetObj = targets.find((t) => t.id === selectedTarget);

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-content">
          <div className="pill">Pixel Mosaic Studio</div>
          <h1>PixMo</h1>
          <p>
            写真を素材タイルで再構成し、密度の高いフォトモザイクを生成します。大きな画像ほど
            精細に仕上がります。
          </p>
          <div className="hero-actions">
            <button
              className="btn primary"
              onClick={() => document.getElementById("target-panel")?.scrollIntoView({ behavior: "smooth" })}
            >
              画像を選ぶ
            </button>
            <button
              className="btn ghost"
              onClick={() => document.getElementById("job-panel")?.scrollIntoView({ behavior: "smooth" })}
            >
              生成へ進む
            </button>
          </div>
        </div>
        <div className="hero-art" aria-hidden>
          <div className="art-grid" />
          <div className="art-orb" />
          <div className="art-frame" />
        </div>
      </header>

      <section className="grid">
        <div className="panel" id="target-panel">
          <div className="panel-header">
            <div>
              <h2>1) ターゲット画像</h2>
              <p>モザイク化する元画像をアップロードします。</p>
            </div>
            <span className="badge">PNG / JPG / WEBP</span>
          </div>

          <div className="panel-body">
            <div className="row">
              <label className="file">
                <input type="file" accept="image/*" onChange={(e) => setTargetFile(e.target.files?.[0] ?? null)} />
                <span>{targetFile ? targetFile.name : "ファイルを選択"}</span>
              </label>
              <button className="btn" onClick={uploadTarget}>アップロード</button>
            </div>

            <div className="list">
              {targets.length === 0 && <div className="muted">まだターゲットがありません</div>}
              {targets.map((t) => (
                <label key={t.id} className={`list-item ${selectedTarget === t.id ? "active" : ""}`}>
                  <input
                    type="radio"
                    name="target"
                    checked={selectedTarget === t.id}
                    onChange={() => setSelectedTarget(t.id)}
                  />
                  <span className="list-title">{t.name}</span>
                  <span className="list-meta">{t.width} ? {t.height}</span>
                  <button className="btn small" onClick={() => deleteTarget(t.id)}>削除</button>
                </label>
              ))}
            </div>

            {selectedTarget && (
              <div className="preview">
                <div className="preview-title">プレビュー</div>
                <img
                  src={apiUrl(`/api/targets/${selectedTarget}/file?sid=${encodeURIComponent(sessionId)}&v=${Date.now()}`)}
                  alt="target preview"
                />
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>2) 素材セット</h2>
              <p>タイルに使う画像をZIPでまとめてアップロード。</p>
            </div>
            <span className="badge">ZIP</span>
          </div>

          <div className="panel-body">
            <div className="row">
              <input
                className="input"
                value={matName}
                onChange={(e) => setMatName(e.target.value)}
                placeholder="素材セット名"
              />
              <label className="file">
                <input type="file" accept=".zip" onChange={(e) => setMatZip(e.target.files?.[0] ?? null)} />
                <span>{matZip ? matZip.name : "ZIPを選択"}</span>
              </label>
              <button className="btn" onClick={uploadMaterials}>アップロード</button>
            </div>

            <div className="list">
              {materials.length === 0 && <div className="muted">まだ素材セットがありません</div>}
              {materials.map((m) => (
                <label key={m.id} className={`list-item ${selectedMaterial === m.id ? "active" : ""}`}>
                  <input
                    type="radio"
                    name="material"
                    checked={selectedMaterial === m.id}
                    onChange={() => setSelectedMaterial(m.id)}
                  />
                  <span className="list-title">{m.name}</span>
                  <span className="list-meta">
                    [{m.status}] {m.count ? `tiles=${m.count}` : ""}{m.status !== "ready" ? ` / ${m.progress}%` : ""}
                  </span>
                  <span className="list-hint">{m.message}</span>
                  <button className="btn small" onClick={() => deleteMaterial(m.id)}>削除</button>
                </label>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="panel" id="job-panel">
        <div className="panel-header">
          <div>
            <h2>3) 生成設定</h2>
            <p>ディテールと雰囲気を調整して、理想のモザイクへ。</p>
          </div>
          <span className="badge">Mosaic</span>
        </div>

        <div className="panel-body">
          <div className="controls">
            <div className="control">
              <div className="control-head">
                <span>タイルサイズ</span>
                <b>{tileSize}px</b>
              </div>
              <input
                type="range"
                min={8}
                max={256}
                step={1}
                value={tileSize}
                onChange={(e) => setTileSize(Number(e.target.value))}
              />
              <div className="helper">小さくすると精細になりますが時間がかかります。</div>
            </div>

            <div className="control">
              <div className="control-head">
                <span>連続回避</span>
                <b>{noRepeatK}</b>
              </div>
              <input
                type="range"
                min={0}
                max={30}
                step={1}
                value={noRepeatK}
                onChange={(e) => setNoRepeatK(Number(e.target.value))}
              />
              <div className="helper">同じタイルが近くに並ぶのを防ぎます。</div>
            </div>

            <div className="control">
              <div className="control-head">
                <span>色合わせ</span>
                <b>{colorStrength.toFixed(2)}</b>
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={colorStrength}
                onChange={(e) => setColorStrength(Number(e.target.value))}
              />
              <div className="helper">素材の色味をターゲットに近づけます。</div>
            </div>

            <div className="control">
              <div className="control-head">
                <span>元画像オーバーレイ</span>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={overlayEnabled}
                    onChange={(e) => setOverlayEnabled(e.target.checked)}
                  />
                  <span className="switch-slider" />
                </label>
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={overlayStrength}
                disabled={!overlayEnabled}
                onChange={(e) => setOverlayStrength(Number(e.target.value))}
              />
              <div className="helper">ONにすると元画像の輪郭が残ります。</div>
            </div>
          </div>

          <div className="row">
            <button className="btn primary" onClick={startJob}>生成スタート</button>
            {selectedTargetObj && (
              <span className="muted">サイズ: {selectedTargetObj.width} ? {selectedTargetObj.height}</span>
            )}
          </div>

          {jobId && (
            <div className="job">
              <div className="job-meta">
                <span>job</span>
                <code>{jobId}</code>
              </div>
              <div className="job-meta">
                <span>status</span>
                <b>{status}</b> / {progress}%
              </div>
              <div className="progress">
                <div style={{ width: `${progress}%` }} />
              </div>
              <div className="muted">{message}</div>
            </div>
          )}
        </div>
      </section>

      {resultUrl && (
        <section className="panel result">
          <div className="panel-header">
            <div>
              <h2>Result</h2>
              <p>生成結果をダウンロードできます。</p>
            </div>
            <a className="btn ghost" href={resultUrl} download>
              ダウンロード
            </a>
          </div>
          <div className="panel-body">
            <img src={resultUrl} alt="mosaic result" />
          </div>
        </section>
      )}
    </div>
  );
}
