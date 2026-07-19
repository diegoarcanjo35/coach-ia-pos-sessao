import "./styles.css";
export const metadata = { title: "Coach IA Pós-Sessão", description: "Estudo de poker após a sessão" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR"><body>{children}</body></html>;
}
