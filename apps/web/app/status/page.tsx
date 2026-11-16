import { HealthStatus } from "@/components/HealthStatus";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-[740px] flex-1 flex-col justify-center gap-8 px-6 py-16">
      <header className="flex flex-col gap-3">
        <p className="eyebrow">Status</p>
        <h1 className="serif text-[46px] leading-[1.1]">Revlane</h1>
        <p className="text-[15px] leading-relaxed text-secondary">
          What happened to every rupee between checkout and the bank account. This
          page shows whether the web app can reach the API.
        </p>
      </header>
      <HealthStatus />
    </main>
  );
}
