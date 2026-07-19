export function GET() {
  return Response.json({ status: "ok", service: "coach-ia-web", version: "3.0.0-internal" });
}
