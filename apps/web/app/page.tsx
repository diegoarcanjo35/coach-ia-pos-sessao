"use client";
import { FormEvent, useState } from "react";

type UploadState = "idle" | "sending" | "done" | "error";
type Processing = { session_id: string; status: string; manifest?: any };

export default function Home() {
  const [state, setState] = useState<UploadState>("idle");
  const [message, setMessage] = useState("Selecione uma gravação encerrada da PPPoker.");
  const [authenticated, setAuthenticated] = useState(false);
  const [processing, setProcessing] = useState<Processing | null>(null);

  async function followProcessing(sessionId: string) {
    for (let attempt = 0; attempt < 30; attempt++) {
      const response = await fetch(`/v1/sessions/${sessionId}/processing`, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        setProcessing(data);
        if (["hands_detected", "failed"].includes(data.status)) return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const response = await fetch("/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) });
    if (response.ok) { setAuthenticated(true); setMessage("Acesso seguro liberado. Selecione uma gravação encerrada."); }
    else setMessage("E-mail ou senha inválidos.");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setState("sending"); setProcessing(null); setMessage("Enviando gravação com segurança…");
    try {
      const response = await fetch("/v1/uploads", { method: "POST", body: new FormData(event.currentTarget) });
      if (!response.ok) throw new Error("upload");
      const session = await response.json();
      formElement.reset();
      setState("done"); setMessage(`Sessão ${String(session.id).slice(0, 8)} recebida. Acompanhe a validação abaixo.`);
      void followProcessing(String(session.id));
    } catch { setState("error"); setMessage("Não foi possível enviar. Confirme se a API está ativa e tente novamente."); }
  }

  const timeline = processing?.manifest?.timeline ?? [];
  const metadata = processing?.manifest?.metadata;
  return <main>
    <nav><span className="mark">C</span><strong>Coach IA</strong><span className="badge">PÓS-SESSÃO</span></nav>
    <section className="hero">
      <p className="eyebrow">PPPOKER · ESTUDO TÉCNICO</p><h1>Sua sessão termina.<br/><span>Seu estudo começa.</span></h1>
      <p className="lead">Envie a gravação depois de jogar. A plataforma prepara a revisão — nunca durante a partida.</p>
      {!authenticated ? <form key="login" onSubmit={login}>
        <label>E-mail administrativo<input name="email" type="email" autoComplete="username" required /></label>
        <label>Senha<input name="password" type="password" autoComplete="current-password" required /></label>
        <button>Entrar com segurança</button><p className="status">{message}</p>
      </form> : <form key="upload" onSubmit={submit}>
        <label>Gravação da sessão<input name="video" type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm" required /></label>
        <label>Nome do torneio (opcional)<input name="tournament_name" autoComplete="off" data-lpignore="true" data-1p-ignore placeholder="Ex.: 20K Garantido" /></label>
        <button disabled={state === "sending"}>{state === "sending" ? "Enviando…" : "Enviar sessão"}</button><p className={`status ${state}`}>{message}</p>
        {processing && <div className="processing"><strong>Estado: {processing.status}</strong>
          {metadata && <span>{metadata.duration_seconds}s · {metadata.video.width}×{metadata.video.height} · {metadata.video.codec}</span>}
          {processing.manifest?.hand_detection?.summary && <span>{processing.manifest.hand_detection.summary.hands_detected} mãos detectadas · {processing.manifest.hand_detection.summary.with_lobby} com Lobby · {processing.manifest.hand_detection.summary.partial} parcial</span>}
          <div className="timeline">{timeline.slice(0, 12).map((frame: any) => <figure key={frame.file}>
            <img src={`/v1/sessions/${processing.session_id}/frames/${frame.file}`} alt={`Frame em ${frame.timestamp_seconds}s`} />
            <figcaption>{frame.timestamp_seconds}s · S{frame.segment_id}</figcaption>
          </figure>)}</div>
        </div>}
      </form>}
    </section>
    <section className="features"><article><b>01</b><h2>Upload manual</h2><p>Você controla quando a análise começa.</p></article><article><b>02</b><h2>Processamento assíncrono</h2><p>Nenhuma orientação durante o jogo.</p></article><article><b>03</b><h2>Evidência primeiro</h2><p>A IA não inventa ações não comprovadas.</p></article></section>
  </main>;
}
