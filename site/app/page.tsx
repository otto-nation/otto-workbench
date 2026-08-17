import Link from 'next/link';

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4">
      <h1 className="text-2xl font-bold">otto-workbench</h1>
      <Link href="/docs/getting-started" className="underline">
        Read the docs
      </Link>
    </main>
  );
}
