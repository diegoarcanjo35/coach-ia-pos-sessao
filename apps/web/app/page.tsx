"use client";
import { FormEvent, useEffect, useState } from "react";

type UploadState = "idle" | "sending" | "done" | "error";
type Processing = { session_id: string; status: string; manifest?: any };
type SessionSummary = { id: string; tournament_name?: string; original_filename?: string; created_at: string; processing_status: string; complete_hands: number; partial_hands: number };

export default function Home() {
  const [state, setState] = useState<UploadState>("idle");
  const [message, setMessage] = useState("Selecione uma gravação encerrada da PPPoker.");
  const [authenticated, setAuthenticated] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [processing, setProcessing] = useState<Processing | null>(null);
  const [history, setHistory] = useState<SessionSummary[]>([]);

  async function loadHistory() {
    const response = await fetch("/v1/sessions", { cache: "no-store" });
    if (response.ok) setHistory(await response.json());
  }

  useEffect(() => {
    void (async () => {
      const response = await fetch("/v1/auth/me", { cache: "no-store" });
      if (response.ok) { setAuthenticated(true); await loadHistory(); }
      setAuthChecked(true);
    })();
  }, []);

  async function followProcessing(sessionId: string) {
    for (let attempt = 0; attempt < 30; attempt++) {
      const response = await fetch(`/v1/sessions/${sessionId}/processing`, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        setProcessing(data);
        if (["clips_ready_for_review", "failed"].includes(data.status)) { await loadHistory(); return; }
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const response = await fetch("/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) });
    if (response.ok) { setAuthenticated(true); setMessage("Acesso seguro liberado. Selecione uma gravação encerrada."); await loadHistory(); }
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
      await loadHistory();
    } catch { setState("error"); setMessage("Não foi possível enviar. Confirme se a API está ativa e tente novamente."); }
  }

  const timeline = processing?.manifest?.timeline ?? [];
  const metadata = processing?.manifest?.metadata;
  if (!authChecked) return <main><p className="loading">Verificando sessão segura…</p></main>;
  return <main>
    <nav><span className="mark">C</span><strong>Coach IA</strong><span className="badge">PÓS-SESSÃO</span>{authenticated && <button className="logout" onClick={async()=>{await fetch("/v1/auth/logout",{method:"POST"});setAuthenticated(false);setHistory([]);setProcessing(null)}}>Sair</button>}</nav>
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
          {processing.manifest?.hand_detection?.summary && <span>{processing.manifest.hand_detection.summary.complete_hands} mãos completas · {processing.manifest.hand_detection.summary.partial} candidato incompleto · {processing.manifest.hand_detection.summary.with_lobby} com Lobby</span>}
          {processing.manifest?.lobby_context && <span>{processing.manifest.lobby_context.length} evento(s) de Lobby · {(processing.manifest.rabbit_detection?.candidates ?? []).length} evidências de Coelho aguardando OCR</span>}
          <div className="timeline">{timeline.slice(0, 12).map((frame: any) => <figure key={frame.file}>
            <img src={`/v1/sessions/${processing.session_id}/frames/${frame.file}`} alt={`Frame em ${frame.timestamp_seconds}s`} />
            <figcaption>{frame.timestamp_seconds}s · S{frame.segment_id}</figcaption>
          </figure>)}</div>
          <div className="clips">{(processing.manifest?.clips ?? []).map((clip: any) => <article key={clip.file}>
            <video controls preload="metadata" src={`/v1/sessions/${processing.session_id}/clips/${clip.file}`} />
            <span>Mão {clip.hand_index} · {clip.partial ? "INCOMPLETA — REVISAR" : "completa"}</span>
          </article>)}</div>
        </div>}
      </form>}
    </section>
    {authenticated && <section className="history"><h2>Sessões recentes</h2><div className="history-grid">{history.map(item=><button key={item.id} onClick={()=>void followProcessing(item.id)}>
      <strong>{item.tournament_name || "Sessão PPPoker"}</strong><span>{new Date(item.created_at).toLocaleString("pt-BR")}</span><span>{item.processing_status}</span><small>{item.complete_hands} completas · {item.partial_hands} incompletas</small>
    </button>)}</div></section>}
    <section className="features"><article><b>01</b><h2>Upload manual</h2><p>Você controla quando a análise começa.</p></article><article><b>02</b><h2>Processamento assíncrono</h2><p>Nenhuma orientação durante o jogo.</p></article><article><b>03</b><h2>Evidência primeiro</h2><p>A IA não inventa ações não comprovadas.</p></article></section>
  </main>;
}
