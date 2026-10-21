// Next.js instrumentation hook (https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation).
// Runs once per server boot — including the standalone server the launcher
// starts — before request handling is fully initialized.
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { stampConnectionAddress } = await import("./lib/connection-stamp");
  stampConnectionAddress();
}
