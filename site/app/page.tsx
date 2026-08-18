import { GrecaDivider } from '@/components/greca';
import { Footer } from '@/components/landing/footer';
import { Hero } from '@/components/landing/hero';
import { HowItWorks } from '@/components/landing/how-it-works';
import { Included } from '@/components/landing/included';
import { Install } from '@/components/landing/install';
import { Nav } from '@/components/landing/nav';

export default function Home() {
  return (
    <main className="flex-1">
      <Nav />
      <Hero />
      <Install />
      <GrecaDivider />
      <Included />
      <HowItWorks />
      <Footer />
    </main>
  );
}
