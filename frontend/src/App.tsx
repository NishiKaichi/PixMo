import { useEffect, useMemo, useState } from "react";
import "./App.css";

type JobStatus = "queued" | "running" | "done" | "error";

export default function App() {
  const [target, setTarget] = useState<File | null>(null);
  const [tilesZip, setTilesZip] = useState<File | null>(null);

  const [tileSize, setTileSize] = useState<number>(32);
  const [outWidth, setOutWidth] = useState<number>(1200);

  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState<number>(0);
  const [message, setMessage] = useState<string>("");

  const resultUrl = useMemo(() => {
    if (!jobId || status !== "done") return null;
    return `/api/jobs/${jobId}/result?v=${Date.now()}`; // キャッシュ回避
  }, [jobId, status]);

  async function startJob() {
    if (!target || !tilesZip) {
      alert("元画像と素材ZIPを選んでください");
      return;
    }

    const fd = new FormData();
    fd.append("target_image", target);
    fd.append("tiles_zip", tilesZip);
    fd.append("tile_size", String(tileSize));
    fd.append("out_width", String(outWidth));

    setMessage("Uploading...");
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) {
      const txt = await res.text();
      alert(`作成失敗: ${txt}`);
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

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: 24, fontFamily: "system-ui" }}>
      <h1 style={{ marginBottom: 8 }}>PixMo (Photo Mosaic)</h1>
      <p style={{ marginTop: 0, color: "#666" }}>
        元画像を、素材画像タイルで再構成します（MVP版）
      </p>

      <div style={{ display: "grid", gap: 12, padding: 16, border: "1px solid #ddd", borderRadius: 12 }}>
        <label>
          元画像（jpg/png/webp）
          <input type="file" accept="image/*" onChange={(e) => setTarget(e.target.files?.[0] ?? null)} />
        </label>

        <label>
          素材画像ZIP（中身はjpg/png/webp）
          <input type="file" accept=".zip" onChange={(e) => setTilesZip(e.target.files?.[0] ?? null)} />
        </label>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label>
            タイルサイズ
            <input
              type="number"
              value={tileSize}
              min={8}
              max={128}
              onChange={(e) => setTileSize(Number(e.target.value))}
              style={{ marginLeft: 8, width: 90 }}
            />
          </label>

          <label>
            出力幅(px)
            <input
              type="number"
              value={outWidth}
              min={400}
              max={4000}
              onChange={(e) => setOutWidth(Number(e.target.value))}
              style={{ marginLeft: 8, width: 110 }}
            />
          </label>

          <button onClick={startJob} style={{ padding: "8px 14px", borderRadius: 10, cursor: "pointer" }}>
            生成開始
          </button>
        </div>

        {jobId && (
          <div style={{ display: "grid", gap: 8 }}>
            <div>job: <code>{jobId}</code></div>
            <div>status: <b>{status}</b> / {progress}%</div>
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
