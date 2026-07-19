"use client";
import { FormEvent, useState } from "react";

type UploadState = "idle" | "sending" | "done" | "error";

export default function Home() {
  const [state, setState] = useState<UploadState>("idle");
  const [message, setMessage] = useState("Selecione uma gravação encerrada da PPPoker.");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("sending");
    setMessage("Enviando gravação com segurança…");
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1/uploads`, { method: "POST", body: form });
      if (!response.ok) throw new Error("upload");
      const session = await response.json();
      setState("done");
      setMessage(`Sessão ${String(session.id).slice(0, 8)} recebida e colocada na fila.`);
    } catch {
      setState("error");
      setMessage("Não foi possível enviar. Confirme se a API está ativa e tente novamente.");
    }
  }

  return <main>
    <nav><span className="mark">C</span><strong>Coach IA</strong><span className="badge">PÓS-SESSÃO</span></nav>
    <section className="hero">
      <p className="eyebrow">PPPOKER · ESTUDO TÉCNICO</p>
      <h1>Sua sessão termina.<br/><span>Seu estudo começa.</span></h1>
      <p className="lead">Envie a gravação depois de jogar. A plataforma organizará as mãos e preparará a revisão — nunca durante a partida.</p>
      <form onSubmit={submit}>
        <label>Gravação da sessão<input name="video" type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm" required /></label>
        <label>Nome do torneio (opcional)<input name="tournament_name" placeholder="Ex.: 20K Garantido" /></label>
        <button disabled={state === "sending"}>{state === "sending" ? "Enviando…" : "Enviar sessão"}</button>
        <p className={`status ${state}`}>{message}</p>
      </form>
    </section>
    <section className="features"><article><b>01</b><h2>Upload manual</h2><p>Você controla quando a análise começa.</p></article><article><b>02</b><h2>Processamento assíncrono</h2><p>Nenhuma orientação durante o jogo.</p></article><article><b>03</b><h2>Evidência primeiro</h2><p>A IA não inventa ações que o vídeo não comprova.</p></article></section>
  </main>;
}
