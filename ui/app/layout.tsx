import "./globals.css";

export const metadata = {
  title: "Procurement Approval Inbox",
  description: "Human-in-the-Loop inbox for procurement approvals",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body style={{ fontFamily: "system-ui", margin: 0, background: "#f6f7f9" }}>
        <header style={{ background: "#0f172a", color: "white", padding: "12px 20px" }}>
          <h1 style={{ margin: 0, fontSize: 18 }}>Procurement — Approval Inbox</h1>
          <small>Enterprise Agentic AI Platform · HITL</small>
        </header>
        <main style={{ maxWidth: 1100, margin: "20px auto", padding: "0 16px" }}>{children}</main>
      </body>
    </html>
  );
}
