import { useEffect, useMemo, useState } from "react";

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
  // ---- Libraries ----
  const [materials, setMaterials] = useState<Material[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);

  const [selectedMaterial, setSelectedMaterial] = useState<string>("");
  const [selectedTarget, setSelectedTarget] = useState<string>("");

  // Upload inputs
  const [matZip, setMatZip] = useState<File | null>(null);
  const [matName, setMatName] = useState<string>("materials");

  const [targetFile, setTargetFile] = useState<File | null>(null);

  // Job params
  const [tileSize, setTileSize] = useState<number>(32);

  // Job state
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState<number>(0);
  const [message, setMessage] = useState<string>("");

  const resultUrl = useMemo(() => {
    if (!jobId || status !== "done") return null;
    return `/api/jobs/${jobId}/result?v=${Date.now()}`;
  }, [jobId, status]);

  async function refreshMaterials() {
    const res = await fetch("/api/materials");
    const data = await res.json();
    setMaterials(data.materials);
    // 初期選択
    if (!selectedMaterial && data.materials?.length) {
      const ready = data.materials.find((m: Material) => m.status === "ready") || data.materials[0];
      setSelectedMaterial(ready.id);
    }
  }

  async function refreshTargets() {
    const res = await fetch("/api/targets");
    const data = await res.json();
    setTargets(data.targets);
    if (!selectedTarget && data.targets?.length) {
      setSelectedTarget(data.targets[0].id);
    }
  }

  useEffect(() => {
    refreshMaterials();
    refreshTargets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 素材がprocessingなら定期更新（進捗表示用）
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

    const res = await fetch("/api/materials", { method: "POST", body: fd });
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

    const res = await fetch("/api/targets", { method: "POST", body: fd });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshTargets();
    setTargetFile(null);
  }

  async function deleteMaterial(id: string) {
    if (!confirm("この素材セットを削除しますか？")) return;
    const res = await fetch(`/api/materials/${id}`, { method: "DELETE" });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshMaterials();
    if (selectedMaterial === id) setSelectedMaterial("");
  }

  async function deleteTarget(id: string) {
    if (!confirm("このターゲット画像を削除しますか？")) return;
    const res = await fetch(`/api/targets/${id}`, { method: "DELETE" });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    await refreshTargets();
    if (selectedTarget === id) setSelectedTarget("");
  }

  async function startJob() {
    if (!selectedTarget || !selectedMaterial) {
      alert("ターゲットと素材セットを選んでください");
      return;
    }
    const mat = materials.find((m) => m.id === selectedMaterial);
    if (mat && mat.status !== "ready") {
      alert("素材セットがreadyではありません（処理中/エラーの可能性）");
      return;
    }

    const fd = new FormData();
    fd.append("target_id", selectedTarget);
    fd.append("material_id", selectedMaterial);
    fd.append("tile_size", String(tileSize));

    setMessage("Creating job...");
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
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

  // ジョブ進捗ポーリング
  useEffect(() => {
    if (!jobId) return;

    const timer = setInterval(async () => {
      const res = await fetch(`/api/jobs/${jobId}`);
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
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 24, fontFamily: "system-ui" }}>
      <h1 style={{ marginBottom: 6 }}>PixMo</h1>
      <p style={{ marginTop: 0, color: "#666" }}>
        素材セット/ターゲットを登録して、選んで生成（アプリ起動中は保持）
      </p>

      {/* Targets */}
      <div style={{ border: "1px solid #ddd", borderRadius: 12, padding: 16, marginBottom: 14 }}>
        <h2 style={{ marginTop: 0 }}>1) ターゲット画像（登録・選択）</h2>

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <input type="file" accept="image/*" onChange={(e) => setTargetFile(e.target.files?.[0] ?? null)} />
          <button onClick={uploadTarget} style={{ padding: "8px 12px", borderRadius: 10 }}>
            登録
          </button>
        </div>

        <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
          {targets.length === 0 && <div style={{ color: "#888" }}>まだターゲットがありません</div>}
          {targets.map((t) => (
            <label key={t.id} style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <input
                type="radio"
                name="target"
                checked={selectedTarget === t.id}
                onChange={() => setSelectedTarget(t.id)}
              />
              <span>
                {t.name} <span style={{ color: "#666" }}>({t.width}×{t.height})</span>
              </span>
              <button
                onClick={() => deleteTarget(t.id)}
                style={{ marginLeft: "auto", padding: "6px 10px", borderRadius: 10 }}
              >
                削除
              </button>
            </label>
          ))}
        </div>

        {selectedTarget && (
          <div style={{ marginTop: 12 }}>
            <div style={{ color: "#666", marginBottom: 6 }}>プレビュー</div>
            <img
              src={`/api/targets/${selectedTarget}/file?v=${Date.now()}`}
              style={{ maxWidth: "100%", borderRadius: 12, border: "1px solid #ddd" }}
            />
          </div>
        )}
      </div>

      {/* Materials */}
      <div style={{ border: "1px solid #ddd", borderRadius: 12, padding: 16, marginBottom: 14 }}>
        <h2 style={{ marginTop: 0 }}>2) 素材セット（登録・選択）</h2>

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <input
            value={matName}
            onChange={(e) => setMatName(e.target.value)}
            placeholder="素材セット名"
            style={{ padding: 8, borderRadius: 10, border: "1px solid #ccc" }}
          />
          <input type="file" accept=".zip" onChange={(e) => setMatZip(e.target.files?.[0] ?? null)} />
          <button onClick={uploadMaterials} style={{ padding: "8px 12px", borderRadius: 10 }}>
            登録（ZIP）
          </button>
        </div>

        <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
          {materials.length === 0 && <div style={{ color: "#888" }}>まだ素材セットがありません</div>}

          {materials.map((m) => (
            <label key={m.id} style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <input
                type="radio"
                name="material"
                checked={selectedMaterial === m.id}
                onChange={() => setSelectedMaterial(m.id)}
              />
              <span>
                {m.name}{" "}
                <span style={{ color: "#666" }}>
                  [{m.status}] {m.count ? `tiles=${m.count}` : ""}
                  {m.status !== "ready" ? ` / ${m.progress}%` : ""}
                </span>
              </span>
              <span style={{ color: "#888", marginLeft: 8 }}>{m.message}</span>

              <button
                onClick={() => deleteMaterial(m.id)}
                style={{ marginLeft: "auto", padding: "6px 10px", borderRadius: 10 }}
              >
                削除
              </button>
            </label>
          ))}
        </div>
      </div>

      {/* Job */}
      <div style={{ border: "1px solid #ddd", borderRadius: 12, padding: 16 }}>
        <h2 style={{ marginTop: 0 }}>3) 生成</h2>

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label>
            タイルサイズ
            <input
              type="number"
              min={8}
              max={128}
              value={tileSize}
              onChange={(e) => setTileSize(Number(e.target.value))}
              style={{ marginLeft: 8, width: 90 }}
            />
          </label>

          <button onClick={startJob} style={{ padding: "8px 14px", borderRadius: 10, cursor: "pointer" }}>
            生成開始
          </button>

          {selectedTargetObj && (
            <span style={{ color: "#666" }}>
              出力サイズ：{selectedTargetObj.width}×{selectedTargetObj.height}（ターゲットと同一）
            </span>
          )}
        </div>

        {jobId && (
          <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
            <div>
              job: <code>{jobId}</code>
            </div>
            <div>
              status: <b>{status}</b> / {progress}%
            </div>
            <div style={{ height: 10, background: "#eee", borderRadius: 999 }}>
              <div style={{ width: `${progress}%`, height: "100%", background: "#333", borderRadius: 999 }} />
            </div>
            <div style={{ color: "#666" }}>{message}</div>
          </div>
        )}
      </div>

      {resultUrl && (
        <div style={{ marginTop: 20 }}>
          <h2>Result</h2>
          <a href={resultUrl} download style={{ display: "inline-block", marginBottom: 10 }}>
            ダウンロード
          </a>
          <div>
            <img src={resultUrl} style={{ maxWidth: "100%", borderRadius: 12, border: "1px solid #ddd" }} />
          </div>
        </div>
      )}
    </div>
  );
}
