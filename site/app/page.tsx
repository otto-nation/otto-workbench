import {
  Button,
  CardGrid,
  Eyebrow,
  Footer,
  GrecaDivider,
  Hero,
  InstallBlock,
  Nav,
  type CardItem,
} from '@otto-nation/brand';
import { SearchButton } from '@otto-nation/brand/search-button';

// SearchButton is imported from its own subpath, not the barrel: it is the only
// component that touches fumadocs-ui, and re-exporting it from the barrel would
// pull fumadocs into the graph of every consumer, including docs-less ones.

const ITEMS: CardItem[] = [
  { title: 'Scripts', href: '/docs/tools#scripts', body: 'Workbench utilities for environment management, validation, and code generation' },
  { title: 'Shell', href: '/docs/architecture#shell-zsh', body: 'ZSH with modular config layers, Starship prompt, and lazy-loaded plugins' },
  { title: 'Git', href: '/docs/architecture#git', body: 'Two-layer gitconfig, global hooks, and conventional commit conventions' },
  { title: 'Tools', href: '/docs/tools#installed-tools', body: 'CLI tools managed via Homebrew, organized by domain' },
  { title: 'AI', href: '/docs/ai-automation', body: 'Claude Code integration with skills, agents, guidelines, and git automation' },
  { title: 'Task automation', href: '/docs/ai-automation#task-automation', body: 'Global Taskfile for AI-powered commits, PRs, and reviews' },
];

const TIERS: CardItem[] = [
  { title: 'Core', accent: 'var(--ow-amarillo)', body: 'Always synced, on every machine', meta: 'bin · git · task · zsh' },
  { title: 'Optional', accent: 'var(--ow-rosa)', body: 'Opt in from the install menu', meta: 'brew · docker · terminals · editors · ai · mise' },
];

const INSTALL = [
  'brew install otto-nation/tap/otto-workbench',
  'otto-workbench install',
  'exec zsh',
];

export default function Home() {
  return (
    <>
      <Nav
        product="otto-workbench"
        links={[
          { label: 'docs', href: '/docs/getting-started' },
          { label: 'github', href: 'https://github.com/otto-nation/otto-workbench' },
        ]}
        slot={<SearchButton />}
      />
      <main className="flex-1">
        <Hero
          eyebrow="ENVIRONMENT MANAGER"
          headline={
            <>
              One command.
              <br />
              Every machine.
            </>
          }
          lede="Shell config, git settings, brew packages, editor preferences, and AI coding tools — managed through a component framework that keeps everything reproducible and in sync."
          actions={
            <>
              <Button href="/docs/getting-started">Get started</Button>
              <Button href="https://github.com/otto-nation/otto-workbench" variant="outline">
                GitHub
              </Button>
            </>
          }
        />
        <section className="px-6 pb-8">
          <InstallBlock shell="zsh" commands={INSTALL} />
        </section>
        <GrecaDivider />
        <section className="px-6 py-7">
          <Eyebrow className="mb-5">WHAT&apos;S INCLUDED</Eyebrow>
          <CardGrid columns={3} items={ITEMS} />
        </section>
        <section className="px-6 pb-8">
          <Eyebrow className="mb-4">HOW IT WORKS</Eyebrow>
          <CardGrid columns={2} items={TIERS} />
        </section>
      </main>
      <Footer
        cta={
          <Button href="/docs/getting-started" variant="outline" onDark>
            Read the docs →
          </Button>
        }
      />
    </>
  );
}
