import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

// Backend address (override with VITE_API_URL in frontend/.env)
const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

// The four pipeline stages, in order. `step` matches the backend event names.
const STEPS = [
  { step: "search", label: "Search", desc: "Finding sources on the web" },
  { step: "reader", label: "Reader", desc: "Scraping the best source" },
  { step: "writer", label: "Writer", desc: "Drafting the report" },
  { step: "critic", label: "Critic", desc: "Reviewing the report" },
];

// Explanatory overview cards shown below the research workspace
const WORKFLOW_STAGES = [
  { num: "01", title: "Search", desc: "Retrieve relevant web results using Tavily." },
  { num: "02", title: "Read", desc: "Select and extract relevant source content." },
  { num: "03", title: "Write", desc: "Generate a structured research report." },
  { num: "04", title: "Critique", desc: "Evaluate the report and provide a score." },
];

const TECH_STACK = ["Python", "LangChain", "Groq", "Tavily", "FastAPI", "React", "Vite"];

// ---- Metadata helpers: derive values only from real backend output ----

// Each result returned by the search tool contains one "URL:..." line,
// so counting those lines gives the number of sources actually retrieved.
// (Returns null if the search failed or produced no results.)
function countSources(searchText) {
  if (!searchText) return null;
  const n = (searchText.match(/^URL:/gm) || []).length;
  return n > 0 ? n : null;
}

// The critic replies in the format "Score: X/10". Returns "X/10" or null.
function parseScore(criticText) {
  if (!criticText) return null;
  const m = criticText.match(/Score:\s*\**\s*(\d+(?:\.\d+)?)\s*\/\s*10/i);
  return m ? `${m[1]}/10` : null;
}

export default function App() {
  const [topic, setTopic] = useState("");
  const [running, setRunning] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState("");
  // status per step: "pending" | "running" | "done"
  const [status, setStatus] = useState({});
  // output per step (search results, scraped text, report, feedback)
  const [data, setData] = useState({});
  const sourceRef = useRef(null);
  // Synchronous guard so a double-click can never start two runs at once
  const runningRef = useRef(false);
  // Topic that was actually researched (the input box may be edited afterwards)
  const [researchedTopic, setResearchedTopic] = useState("");
  // Set when the pipeline finishes successfully
  const [completedAt, setCompletedAt] = useState(null);
  // PDF download state
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState("");
  // Copy report state: "idle" | "copied" | "error"
  const [copyState, setCopyState] = useState("idle");
  const copyTimer = useRef(null);

  // Close the stream and clear the timer if the page is left
  useEffect(() => () => {
    sourceRef.current?.close();
    clearTimeout(copyTimer.current);
  }, []);

  // Start a research run for topic `t`. Used by both the form and "Run Again",
  // so there is only one SSE implementation.
  function runResearch(t) {
    if (!t || runningRef.current) return;
    runningRef.current = true;

    // Make sure no old stream is still open
    sourceRef.current?.close();

    // Reset everything from the previous run
    setStatus({});
    setData({});
    setError("");
    setPdfError("");
    setCopyState("idle");
    setCompletedAt(null);
    setComplete(false);
    setResearchedTopic(t);
    setTopic(t); // keep the topic in the input box
    setRunning(true);

    // Open the SSE stream from GET /research
    const es = new EventSource(`${API_URL}/research?topic=${encodeURIComponent(t)}`);
    sourceRef.current = es;

    es.onmessage = (msg) => {
      if (sourceRef.current !== es) return; // ignore events from an old stream
      const ev = JSON.parse(msg.data);
      if (ev.step === "pipeline" && ev.status === "complete") {
        setCompletedAt(new Date()); // record when the research really finished
        setComplete(true);
        setRunning(false);
        runningRef.current = false;
        es.close(); // stop, otherwise EventSource would reconnect and rerun
        return;
      }
      if (ev.step === "pipeline" && ev.status === "error") {
        setError(ev.detail || "The research pipeline encountered an error.");
        setRunning(false);
        runningRef.current = false;
        es.close();
        return;
      }
      setStatus((s) => ({ ...s, [ev.step]: ev.status }));
      if (ev.status === "done") setData((d) => ({ ...d, [ev.step]: ev.data }));
    };

    es.onerror = () => {
      if (sourceRef.current !== es) return;
      es.close();
      setRunning(false);
      runningRef.current = false;
      setError("Connection to the backend was lost or failed. Is it running, and are the API keys set?");
    };
  }

  function start(e) {
    e.preventDefault();
    runResearch(topic.trim());
  }

  // Run Again: same topic as the last run, no retyping needed
  function runAgain() {
    runResearch(researchedTopic);
  }

  // Copy the report's raw Markdown (not the critic review) to the clipboard
  async function copyReport() {
    clearTimeout(copyTimer.current);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(data.writer);
      } else {
        // Fallback for browsers/contexts without the async clipboard API
        const ta = document.createElement("textarea");
        ta.value = data.writer;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        if (!ok) throw new Error("copy command failed");
      }
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
    // Show the confirmation briefly, then go back to normal
    copyTimer.current = setTimeout(() => setCopyState("idle"), 2000);
  }

  // Send the data we already have to the backend and save the returned PDF.
  // The research pipeline is NOT rerun.
  async function downloadPdf() {
    setPdfLoading(true);
    setPdfError("");
    try {
      const res = await fetch(`${API_URL}/download-pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: researchedTopic,
          report: data.writer,
          critique: data.critic,
        }),
      });
      if (!res.ok) {
        let detail = `Server returned ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      // Use the filename suggested by the server, with a fallback
      const cd = res.headers.get("Content-Disposition") || "";
      const filename = (cd.match(/filename="([^"]+)"/) || [])[1] || "research_report.pdf";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setPdfError(`Could not download the PDF: ${err.message}`);
    } finally {
      setPdfLoading(false);
    }
  }

  // ---- Metadata values (with honest fallbacks) ----
  const sourceCount = countSources(data.search);
  const score = parseScore(data.critic);
  const runStatus = complete ? "Completed" : running ? "In progress" : "Interrupted";
  const completedText = completedAt
    ? completedAt.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : running ? "In progress" : "Not completed";
  const scoreText = score || (running && !data.critic ? "Pending" : "Unavailable");
  const sourcesText = sourceCount ?? (running && !data.search ? "Pending" : "Unavailable");

  return (
    <main className="container">
      <header className="hero">
        <p className="eyebrow">MULTI-AGENT RESEARCH PIPELINE</p>
        <h1>Research that goes beyond the first answer.</h1>
        <p className="subtitle">
          From web search to source analysis, structured reporting and AI critique.
          Turn any topic into a research brief you can explore, examine and download.
        </p>
      </header>

      <section className="search-box" aria-label="Research input">
        <label htmlFor="topic-input" className="input-label">
          WHAT WOULD YOU LIKE TO RESEARCH?
        </label>
        <form onSubmit={start} className="search">
          <input
            id="topic-input"
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Enter a research topic…"
            disabled={running}
          />
          {/* Disabled while a run is in progress, so two runs can't start at once */}
          <button type="submit" disabled={running || !topic.trim()}>
            {running ? "Researching…" : "Research"}
          </button>
        </form>
      </section>

      {error && <div className="error">{error}</div>}

      {/* Progress tracker */}
      {(running || complete || Object.keys(status).length > 0) && (
        <ol className="steps">
          {STEPS.map(({ step, label, desc }) => (
            <li key={step} className={status[step] || "pending"}>
              <span className="dot" />
              <strong>{label}</strong>
              <span className="desc">{desc}</span>
            </li>
          ))}
        </ol>
      )}

      {/* Raw outputs of the early stages (collapsible) */}
      {data.search && (
        <details className="card">
          <summary>Search results</summary>
          <pre>{data.search}</pre>
        </details>
      )}
      {data.reader && (
        <details className="card">
          <summary>Scraped content</summary>
          <pre>{data.reader}</pre>
        </details>
      )}

      {/* Research metadata: only real values from this run */}
      {data.writer && (
        <dl className="metadata" aria-label="Research metadata">
          <div><dt>Topic</dt><dd>{researchedTopic}</dd></div>
          <div><dt>Completed</dt><dd>{completedText}</dd></div>
          <div><dt>Sources retrieved</dt><dd>{sourcesText}</dd></div>
          <div><dt>Critic score</dt><dd>{scoreText}</dd></div>
          <div><dt>Status</dt><dd>{runStatus}</dd></div>
        </dl>
      )}

      {data.writer && (
        <section className="card report">
          <div className="card-header">
            <h2>Report</h2>
            <button
              type="button"
              className="secondary small"
              onClick={copyReport}
              aria-live="polite"
            >
              {copyState === "copied" ? "Copied!" : copyState === "error" ? "Copy failed" : "Copy Report"}
            </button>
          </div>
          <ReactMarkdown>{data.writer}</ReactMarkdown>
        </section>
      )}

      {data.critic && (
        <section className="card critique">
          <h2>Critic feedback</h2>
          <ReactMarkdown>{data.critic}</ReactMarkdown>
        </section>
      )}

      {/* Actions: PDF is enabled only once the whole pipeline has finished */}
      {(data.writer || complete || (researchedTopic && !running)) && (
        <div className="download">
          {(data.writer || complete) && (
            <button
              type="button"
              onClick={downloadPdf}
              disabled={!complete || !data.writer || pdfLoading}
            >
              {pdfLoading ? "Preparing PDF…" : "Download PDF"}
            </button>
          )}
          {researchedTopic && !running && (
            <button type="button" className="secondary" onClick={runAgain}>
              Run Again
            </button>
          )}
          {!complete && running && <span className="hint">Available when the pipeline finishes.</span>}
          {pdfError && <div className="error">{pdfError}</div>}
        </div>
      )}

      {/* Explanatory workflow overview */}
      <section className="workflow" aria-labelledby="workflow-heading">
        <h2 id="workflow-heading">Four stages. One research brief.</h2>
        <p className="workflow-desc">
          A sequential AI-powered workflow that transforms a research topic into a structured, reviewed report.
        </p>
        <ol className="workflow-grid">
          {WORKFLOW_STAGES.map(({ num, title, desc }) => (
            <li key={num} className="workflow-card">
              <h3>
                <span className="stage-num">{num} —</span> {title}
              </h3>
              <p>{desc}</p>
            </li>
          ))}
        </ol>
      </section>

      <footer className="site-footer">
        <div className="footer-grid">
          <div className="footer-col">
            <h2>Built with</h2>
            <ul className="tech-list">
              {TECH_STACK.map((tech) => (
                <li key={tech}>{tech}</li>
              ))}
            </ul>
          </div>
          <div className="footer-col">
            <h2>Explore the project</h2>
            <a
              className="footer-link"
              href="https://github.com/prakhar-tripathi182/multi-agent-system"
              target="_blank"
              rel="noopener noreferrer"
            >
              View Source on GitHub ↗
            </a>
          </div>
        </div>
        <p className="footer-bottom">
          Multi-Agent Research Pipeline — Built for deeper research.
        </p>
      </footer>
    </main>
  );
}
