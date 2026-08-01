import { useState, type ReactNode } from 'react'
import './LandingPage.css'

const capabilities = [
  {
    icon: '⌁',
    title: 'Orchestrate every coding agent',
    copy: 'Connect the agents your teams already use. SACM routes work, applies policy, and keeps every run inside one governed workflow.',
    tone: 'violet',
  },
  {
    icon: '◇',
    title: 'Ship with proof, not promises',
    copy: 'Every change carries signed evidence, approvals, security results, provenance, and a replayable audit trail.',
    tone: 'blue',
  },
  {
    icon: '◎',
    title: 'Control risk in real time',
    copy: 'Define autonomy by repository, team, action, and risk. Safe work flows. Sensitive work stops for human approval.',
    tone: 'coral',
  },
]

const workflow = [
  ['01', 'Connect', 'Link GitHub, Jira, your models, and isolated execution environments.'],
  ['02', 'Define', 'Set policies, approval gates, budgets, and evidence requirements as code.'],
  ['03', 'Delegate', 'Send a task to SACM. The right agents plan, build, review, and verify it.'],
  ['04', 'Prove', 'Release with a signed passport showing exactly what changed and why it is safe.'],
]

const developerTabs = {
  CLI: `# Install the SACM developer CLI
pip install sacm-agent-runtime

# Connect your workspace
sacm connect --repo github.com/acme/payments

# Start a governed mission
sacm run "Add idempotent refunds" --policy production`,
  API: `const mission = await sacm.missions.create({
  repository: "acme/payments",
  objective: "Add idempotent refunds",
  policy: "production",
  evidence: ["tests", "sbom", "security"]
})

console.log(mission.status) // "PLANNING"`,
  Policy: `package delivery.production

default allow := false

allow if {
  input.tests.coverage >= 85
  input.security.critical == 0
  input.evidence.signed == true
}`,
}

function Mark() {
  return <span className="landing-mark" aria-hidden="true">
    <i />
    <i />
    <i />
  </span>
}

function Arrow() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 3.5 13 8l-4.5 4.5" /></svg>
}

function Check({ children }: { children: ReactNode }) {
  return <li><span aria-hidden="true">✓</span>{children}</li>
}

export function LandingPage() {
  const [activeTab, setActiveTab] = useState<keyof typeof developerTabs>('CLI')
  const [copied, setCopied] = useState(false)

  const copyCode = async () => {
    await navigator.clipboard.writeText(developerTabs[activeTab])
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return <main className="landing">
    <nav className="landing-nav" aria-label="Main navigation">
      <a className="landing-brand" href="#top"><Mark /><b>SACM</b></a>
      <div className="landing-links">
        <a href="#product">Product</a>
        <a href="#developers">Developers</a>
        <a href="#enterprise">Enterprise</a>
        <a href="#security">Security</a>
      </div>
      <div className="landing-nav-actions">
        <a className="text-link" href="#console">Sign in</a>
        <a className="nav-cta" href="#developers">Start building <Arrow /></a>
      </div>
    </nav>

    <section className="hero" id="top">
      <div className="hero-glow hero-glow-one" />
      <div className="hero-glow hero-glow-two" />
      <div className="hero-copy">
        <a className="announcement" href="#product"><span>New</span> Enterprise agent governance is here <Arrow /></a>
        <h1>Software delivery,<br /><em>built for the agentic era.</em></h1>
        <p>SACM is the secure control plane that turns AI coding agents into a trusted software delivery workforce.</p>
        <div className="hero-actions">
          <a className="primary-cta" href="#console">Open Mission Control <Arrow /></a>
          <a className="secondary-cta" href="#developers"><span aria-hidden="true">⌘</span> Explore developer tools</a>
        </div>
        <small>No credit card · Open developer APIs · Deploy in your cloud</small>
      </div>

      <div className="product-stage" aria-label="SACM Mission Control preview">
        <div className="stage-orbit orbit-one" />
        <div className="stage-orbit orbit-two" />
        <div className="stage-window">
          <header>
            <div className="window-brand"><Mark /><span>SACM</span></div>
            <div className="window-search">⌕ <span>Search missions...</span><kbd>⌘ K</kbd></div>
            <div className="window-user">MB</div>
          </header>
          <div className="window-body">
            <aside>
              <span className="mini-logo">S</span>
              {['⌂', 'M', 'A', 'G', 'P', 'E'].map((item, index) => <i className={index === 0 ? 'active' : ''} key={item}>{item}</i>)}
            </aside>
            <section>
              <div className="demo-heading">
                <div><small>MISSION CONTROL</small><h2>Good morning, Marcin.</h2><p>Your agent workforce is operating within policy.</p></div>
                <button type="button">＋ New mission</button>
              </div>
              <div className="demo-metrics">
                <article><span>Active missions</span><strong>12</strong><small><b>↑ 18%</b> this week</small></article>
                <article><span>Evidence coverage</span><strong>98.4%</strong><small><b>↑ 2.1%</b> this week</small></article>
                <article><span>Policy compliance</span><strong>100%</strong><small>All gates passing</small></article>
                <article><span>Hours returned</span><strong>146</strong><small>Last 30 days</small></article>
              </div>
              <div className="demo-grid">
                <article className="mission-card">
                  <div className="card-title"><div><small>LIVE MISSIONS</small><h3>Agent activity</h3></div><span>View all</span></div>
                  <div className="mission-row"><i className="running">↗</i><div><b>Harden payment webhooks</b><small>acme/payments · Security agent</small></div><span className="status running">Running</span></div>
                  <div className="mission-row"><i className="review">✓</i><div><b>Upgrade React application</b><small>acme/storefront · Reviewer</small></div><span className="status review">Review</span></div>
                  <div className="mission-row"><i className="done">◆</i><div><b>Add audit export API</b><small>acme/platform · 4 agents</small></div><span className="status done">Shipped</span></div>
                </article>
                <article className="trust-card">
                  <div className="card-title"><div><small>TRUST SCORE</small><h3>Release confidence</h3></div><span>Live</span></div>
                  <div className="trust-ring"><strong>94</strong><span>Excellent</span></div>
                  <ul><li><span>Security</span><b>100</b></li><li><span>Evidence</span><b>96</b></li><li><span>Coverage</span><b>88</b></li></ul>
                </article>
              </div>
            </section>
          </div>
        </div>
      </div>
    </section>

    <section className="customer-strip" aria-label="Designed for modern engineering organizations">
      <p>One control plane for every part of software delivery</p>
      <div><span>GITHUB</span><span>JIRA</span><span>OPENAI</span><span>ANTHROPIC</span><span>CODEX</span><span>OPA</span></div>
    </section>

    <section className="section product-section" id="product">
      <div className="section-intro">
        <span className="section-label">THE CONTROL PLANE</span>
        <h2>Move fast without<br />losing control.</h2>
        <p>Give developers the speed of autonomous agents and give your organization the governance it needs to trust every change.</p>
      </div>
      <div className="capability-grid">
        {capabilities.map((capability) => <article className={`capability ${capability.tone}`} key={capability.title}>
          <span className="capability-icon">{capability.icon}</span>
          <h3>{capability.title}</h3>
          <p>{capability.copy}</p>
          <a href="#developers">Learn more <Arrow /></a>
          <div className="capability-visual">
            {capability.tone === 'violet' && <><span className="agent-node main">S</span><span className="agent-node node-a">AI</span><span className="agent-node node-b">CR</span><span className="agent-node node-c">QA</span><i className="path path-a" /><i className="path path-b" /><i className="path path-c" /></>}
            {capability.tone === 'blue' && <div className="passport"><header><Mark /><span>RELEASE PASSPORT</span><b>VERIFIED</b></header><h4>payments-api · v2.4.0</h4><ul><Check>Tests passed</Check><Check>SBOM attached</Check><Check>Provenance signed</Check></ul></div>}
            {capability.tone === 'coral' && <div className="policy-card"><small>POLICY DECISION</small><span className="decision">Approval required</span><p>Production database migration</p><div><span>Risk</span><b>HIGH</b></div><button type="button">Review change</button></div>}
          </div>
        </article>)}
      </div>
    </section>

    <section className="workflow-section">
      <div className="section workflow-inner">
        <div className="workflow-copy">
          <span className="section-label light">HOW IT WORKS</span>
          <h2>From intent to trusted<br />software, automatically.</h2>
          <p>SACM wraps the entire agentic delivery lifecycle in a durable, observable, policy-governed workflow.</p>
          <a href="#console">See Mission Control <Arrow /></a>
        </div>
        <div className="workflow-list">
          {workflow.map(([number, title, copy], index) => <article className={index === 0 ? 'active' : ''} key={number}>
            <span>{number}</span><div><h3>{title}</h3><p>{copy}</p></div>
          </article>)}
        </div>
      </div>
    </section>

    <section className="section developer-section" id="developers">
      <div className="developer-copy">
        <span className="section-label">BUILT FOR DEVELOPERS</span>
        <h2>Your agents.<br />Your stack. One API.</h2>
        <p>Start locally, integrate in minutes, and scale to thousands of governed missions without changing how your team builds software.</p>
        <ul>
          <Check>Python SDK, REST API, MCP server, and CLI</Check>
          <Check>Works with GitHub, Jira, Codex, Claude, and custom agents</Check>
          <Check>Self-hosted executors keep source code in your environment</Check>
          <Check>Policy-as-code with versioned, testable controls</Check>
        </ul>
        <div className="developer-actions">
          <a className="dark-cta" href="https://github.com/marcinbordowicz4-eng/sacm-agent-runtime">Read the docs <Arrow /></a>
          <a href="https://github.com/marcinbordowicz4-eng/sacm-agent-runtime">View on GitHub</a>
        </div>
      </div>
      <div className="code-window">
        <header>
          <div className="window-dots"><i /><i /><i /></div>
          <div className="code-tabs">{Object.keys(developerTabs).map((tab) => <button type="button" className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab as keyof typeof developerTabs)} key={tab}>{tab}</button>)}</div>
          <button className="copy-button" type="button" onClick={() => void copyCode()}>{copied ? 'Copied!' : 'Copy'}</button>
        </header>
        <pre><code>{developerTabs[activeTab]}</code></pre>
        <footer><span><i /> Connected to SACM Cloud</span><span>api.sacm.io/v1</span></footer>
      </div>
    </section>

    <section className="section enterprise-section" id="enterprise">
      <div className="enterprise-card">
        <div>
          <span className="section-label light">ENTERPRISE READY</span>
          <h2>Governance that scales<br />with your ambition.</h2>
          <p>From one team to a global engineering organization, SACM gives security, platform, and compliance leaders one source of truth.</p>
          <a className="white-cta" href="mailto:hello@sacm.io">Talk to us <Arrow /></a>
        </div>
        <div className="enterprise-grid" id="security">
          <article><span>◈</span><h3>Private execution</h3><p>Isolated, signed jobs run inside your own cloud and network boundary.</p></article>
          <article><span>⌾</span><h3>Enterprise IAM</h3><p>OIDC, scoped credentials, tenant isolation, and least-privilege access.</p></article>
          <article><span>⌁</span><h3>Immutable audit</h3><p>Cryptographically chained events and signed, exportable evidence.</p></article>
          <article><span>△</span><h3>Resilient by design</h3><p>Durable workflows, recovery controls, SLOs, backups, and HA topology.</p></article>
        </div>
      </div>
    </section>

    <section className="final-cta">
      <div className="final-glow" />
      <Mark />
      <h2>Build the future.<br /><em>Ship it with confidence.</em></h2>
      <p>Give your developers an AI-native delivery platform they will love — and your organization can trust.</p>
      <div className="hero-actions">
        <a className="primary-cta" href="#console">Open Mission Control <Arrow /></a>
        <a className="secondary-cta" href="mailto:hello@sacm.io">Talk to an engineer</a>
      </div>
    </section>

    <footer className="landing-footer">
      <div className="footer-brand"><a className="landing-brand" href="#top"><Mark /><b>SACM</b></a><p>Secure Agentic Change Management.<br />Software delivery for the agentic era.</p></div>
      <div><b>Product</b><a href="#product">Platform</a><a href="#security">Security</a><a href="#enterprise">Enterprise</a><a href="#console">Mission Control</a></div>
      <div><b>Developers</b><a href="#developers">Documentation</a><a href="#developers">API reference</a><a href="https://github.com/marcinbordowicz4-eng/sacm-agent-runtime">GitHub</a><a href="#developers">MCP server</a></div>
      <div><b>Company</b><a href="mailto:hello@sacm.io">Contact</a><a href="/security">Trust center</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></div>
      <p className="copyright">© 2026 SACM. Built for trusted autonomy.</p>
    </footer>
  </main>
}
