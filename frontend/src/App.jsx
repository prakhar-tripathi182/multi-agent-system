import { useRef, useState } from "react";
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
  // Topic that was actually researched (the input box may be edited afterwards)
  const [researchedTopic, setResearchedTopic] = useState("");
  // PDF download state
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState("");

  function start(e) {
    e.preventDefault();
    const t = topic.trim();
    if (!t || running) return;

    // Reset state for a new run
    setStatus({});
    setData({});
    setError("");
    setPdfError("");
    setResearchedTopic(t);
    setComplete(false);
    setRunning(true);

    // Open the SSE stream from GET /research
    const es = new EventSource(`${API_URL}/research?topic=${encodeURIComponent(t)}`);
    sourceRef.current = es;

    es.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.step === "pipeline" && ev.status === "complete") {
        setComplete(true);
        setRunning(false);
        es.close(); // stop, otherwise EventSource would reconnect and rerun
        return;
      }
      setStatus((s) => ({ ...s, [ev.step]: ev.status }));
      if (ev.status === "done") setData((d) => ({ ...d, [ev.step]: ev.data }));
    };

    es.onerror = () => {
      es.close();
      setRunning(false);
      setError("Connection to the backend was lost or failed. Is it running, and are the API keys set?");
    };
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

  function stop() {
    sourceRef.current?.close();
    setRunning(false);
  }

  return (
    <main className="container">
      <header>
        <h1>Multi-Agent Research Pipeline</h1>
        <p className="subtitle">
          Four agents search, read, write and critique a report on any topic.
        </p>
      </header>

      <form onSubmit={start} className="search">
        <input
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Enter a research topicâ€¦"
          disabled={running}
        />
        {running ? (
          <button type="button" className="secondary" onClick={stop}>Stop</button>
        ) : (
          <button type="submit" disabled={!topic.trim()}>Research</button>
        )}
      </form>

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

      {data.writer && (
        <section className="card report">
          <h2>Report</h2>
          <ReactMarkdown>{data.writer}</ReactMarkdown>
        </section>
      )}

      {data.critic && (
        <section className="card critique">
          <h2>Critic feedback</h2>
          <ReactMarkdown>{data.critic}</ReactMarkdown>
        </section>
      )}

      {/* Enabled only once the whole pipeline has finished with a report */}
      {(data.writer || complete) && (
        <div className="download">
          <button
            type="button"
            onClick={downloadPdf}
            disabled={!complete || !data.writer || pdfLoading}
          >
            {pdfLoading ? "Preparing PDF…" : "Download PDF"}
          </button>
          {!complete && <span className="hint">Available when the pipeline finishes.</span>}
          {pdfError && <div className="error">{pdfError}</div>}
        </div>
      )}
    </main>
  );
}
