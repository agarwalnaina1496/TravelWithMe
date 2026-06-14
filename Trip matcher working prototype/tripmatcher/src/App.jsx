import { useState } from "react";

// ── Prompts ──────────────────────────────────────────────────────────────────

const SCOUT_EXTRACT = `You are Scout, the intake agent for TripMatcher — a travel-matching service for Indian travelers.

Extract structured trip preferences from the user's free-text and return ONLY a raw JSON object. No markdown, no code blocks, no explanation. Just raw JSON.

{
  "trip_goal": "relaxation|adventure|culture|nature|romance|spiritual|photography|food|honeymoon|null",
  "origin": "city name or null",
  "travel_month": "month name or null",
  "duration_days": number or null,
  "budget_level": "budget|mid|premium|luxury|null",
  "group_type": "solo|couple|family|friends|null",
  "group_size": number or null,
  "exclusions": ["things or places to avoid"],
  "preferences": ["specific interests or must-haves"],
  "flexibility": "fixed|flexible|null"
}

Budget mapping (per person total INR): under ₹10k = budget, ₹10k–₹30k = mid, ₹30k–₹80k = premium, above ₹80k = luxury. Set unknown fields to null. Return raw JSON only.`;

const MERIDIAN = `You are Meridian, the destination matching engine for TripMatcher.

Given structured trip inputs from an Indian traveler, return a destination recommendation as raw JSON only. No markdown, no code blocks, no explanation. Just raw JSON.

Focus on Indian destinations first (hill stations, beaches, heritage cities, forests, backwaters). For premium/luxury budgets also consider Sri Lanka, Nepal, Bhutan, Maldives.

{
  "top_recommendation": {
    "name": "destination name",
    "region": "state or country",
    "why_it_matches": "2-3 sentences on why this fits this traveler's specific goal and constraints",
    "budget_estimate_inr": "estimated total per person cost range in INR",
    "how_to_reach": "best route from origin and approximate travel time",
    "weather_in_month": "what the weather is like during the travel month",
    "best_experiences": ["2-4 specific things to do or see"],
    "tradeoffs": ["1-3 honest weaknesses or things to be aware of"]
  },
  "alternatives": [
    { "name": "...", "region": "...", "one_liner": "one sentence why it is a good alternative" },
    { "name": "...", "region": "...", "one_liner": "one sentence why it is a good alternative" }
  ],
  "confidence": "high|medium|low",
  "failure": null
}

If inputs conflict or are too vague to match, set failure to a plain-English explanation string and top_recommendation to null.`;

const SCOUT_FORMAT = `You are Scout, the friendly voice of TripMatcher.

Convert this destination recommendation JSON into warm, confident plain English. 3-4 short paragraphs:
1. Open with the destination and the core reason it fits — confident, direct, personal. No hedging.
2. Practical details: how to get there from origin, budget expectation, what the weather will be like.
3. What specifically makes this destination right for this traveler's trip goal.
4. One honest sentence on a tradeoff, then briefly name the two alternatives in one sentence.

If the JSON contains a failure field (not null), explain why no destination matched and what the traveler could adjust — keep the same warm, honest tone.

150–200 words total. No headers. No bullet points. Flowing conversational prose. Write like a travel-savvy friend who knows this person well.`;

// ── API helper ────────────────────────────────────────────────────────────────

const callClaude = async (system, userMsg) => {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1000,
      system,
      messages: [{ role: "user", content: userMsg }],
    }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const d = await res.json();
  return d.content.map((b) => b.text || "").join("");
};

const parseJSON = (raw) => {
  try {
    const clean = raw.replace(/```json\n?|```\n?/g, "").trim();
    return JSON.parse(clean);
  } catch {
    return { _raw: raw };
  }
};

// ── Sub-components ────────────────────────────────────────────────────────────

const Dot = ({ status }) => (
  <span
    style={{
      width: 7,
      height: 7,
      borderRadius: "50%",
      display: "inline-block",
      flexShrink: 0,
      background: { idle: "#CBD5E1", processing: "#F59E0B", done: "#22C55E", error: "#EF4444" }[status] || "#CBD5E1",
      animation: status === "processing" ? "pls 1.1s ease-in-out infinite" : "none",
    }}
  />
);

const StatusLabel = ({ status }) => {
  const cfg = { idle: ["Waiting", "#94A3B8"], processing: ["Processing…", "#F59E0B"], done: ["Done", "#22C55E"], error: ["Error", "#EF4444"] };
  const [label, color] = cfg[status] || cfg.idle;
  return (
    <span style={{ fontSize: 10.5, fontWeight: 700, color, letterSpacing: "0.08em", textTransform: "uppercase" }}>
      {label}
    </span>
  );
};

const JSONBlock = ({ data }) => (
  <pre
    style={{
      background: "#0D1526",
      color: "#93C5FD",
      padding: "12px 14px",
      borderRadius: 8,
      fontSize: 11.5,
      lineHeight: 1.65,
      overflowX: "auto",
      overflowY: "auto",
      maxHeight: 240,
      margin: 0,
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
      fontFamily: '"Fira Code","JetBrains Mono","Courier New",monospace',
    }}
  >
    {typeof data === "string" ? data : JSON.stringify(data, null, 2)}
  </pre>
);

const Stage = ({ icon, agent, agentColor, label, status, data, isText }) => {
  const [open, setOpen] = useState(true);
  const hasDone = status === "done" && data != null;

  return (
    <div
      style={{
        borderRadius: 12,
        overflow: "hidden",
        border: `1.5px solid ${status === "processing" ? agentColor + "70" : "#E2E8F0"}`,
        background: "#fff",
        boxShadow: status === "processing" ? `0 0 0 3px ${agentColor}20` : "none",
        transition: "border-color 0.25s, box-shadow 0.25s",
      }}
    >
      <div
        onClick={() => hasDone && setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "11px 16px",
          cursor: hasDone ? "pointer" : "default",
          borderBottom: hasDone && open ? "1px solid #F1F5F9" : "none",
          userSelect: "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 7,
              background: agentColor + "18",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 14,
            }}
          >
            {icon}
          </div>
          <div>
            <span style={{ fontSize: 13, fontWeight: 700, color: agentColor }}>{agent}</span>
            <span style={{ fontSize: 11, color: "#94A3B8", marginLeft: 6, fontWeight: 500 }}>{label}</span>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <Dot status={status} />
          <StatusLabel status={status} />
          {hasDone && (
            <span style={{ color: "#CBD5E1", fontSize: 9, marginLeft: 3 }}>{open ? "▲" : "▼"}</span>
          )}
        </div>
      </div>

      {hasDone && open && (
        <div style={{ padding: 16 }}>
          {isText ? (
            <p
              style={{
                margin: 0,
                fontSize: 14.5,
                lineHeight: 1.8,
                color: "#1E293B",
                fontFamily: 'Georgia, "Times New Roman", serif',
              }}
            >
              {data}
            </p>
          ) : (
            <JSONBlock data={data} />
          )}
        </div>
      )}
    </div>
  );
};

// ── Examples ──────────────────────────────────────────────────────────────────

const EXAMPLES = [
  "Relaxing long weekend with my partner from Bengaluru in October. Mid-range budget. We love nature and good food, want to avoid too much driving.",
  "Solo trip from Mumbai in December. Into heritage, food, culture. Budget ₹20k, 5–6 days, prefer cooler weather.",
  "Family trip from Delhi in May — me, wife, two kids (7 & 10). Need a hill station to beat the heat. Premium budget okay. 4 days.",
];

// ── Main ──────────────────────────────────────────────────────────────────────

export default function TripMatcher() {
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [ran, setRan] = useState(false);
  const [stages, setStages] = useState({
    a: { status: "idle", data: null },
    b: { status: "idle", data: null },
    c: { status: "idle", data: null },
  });

  const set = (k, u) => setStages((p) => ({ ...p, [k]: { ...p[k], ...u } }));

  const reset = () => {
    setRan(false);
    setInput("");
    setStages({ a: { status: "idle", data: null }, b: { status: "idle", data: null }, c: { status: "idle", data: null } });
  };

  const run = async () => {
    if (!input.trim() || running) return;
    setRunning(true);
    setRan(true);
    setStages({ a: { status: "idle", data: null }, b: { status: "idle", data: null }, c: { status: "idle", data: null } });

    try {
      set("a", { status: "processing" });
      const r1 = await callClaude(SCOUT_EXTRACT, input);
      const d1 = parseJSON(r1);
      set("a", { status: "done", data: d1 });

      set("b", { status: "processing" });
      const r2 = await callClaude(MERIDIAN, JSON.stringify(d1));
      const d2 = parseJSON(r2);
      set("b", { status: "done", data: d2 });

      set("c", { status: "processing" });
      const r3 = await callClaude(SCOUT_FORMAT, JSON.stringify(d2));
      set("c", { status: "done", data: r3 });
    } catch {
      setStages((p) => {
        const n = { ...p };
        Object.keys(n).forEach((k) => { if (n[k].status === "processing") n[k] = { ...n[k], status: "error" }; });
        return n;
      });
    } finally {
      setRunning(false);
    }
  };

  const canRun = input.trim() && !running;

  return (
    <>
      <style>{`
        @keyframes pls { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.75)} }
        *{box-sizing:border-box}
        textarea:focus{outline:none}
        button:hover{opacity:.88}
      `}</style>

      <div style={{ fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif', background: "#F8FAFC", minHeight: "100vh" }}>

        {/* Nav */}
        <div style={{ background: "#0F172A", padding: "13px 22px", display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 26, height: 26, borderRadius: 6, background: "#F59E0B20", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13 }}>
            ✈
          </div>
          <span style={{ color: "#F8FAFC", fontWeight: 700, fontSize: 14, letterSpacing: "-0.01em" }}>TripMatcher</span>
          <span style={{ color: "#475569", fontSize: 11 }}>by TravelWithMe</span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            {ran && !running && (
              <button
                onClick={reset}
                style={{ fontSize: 11.5, padding: "5px 12px", borderRadius: 6, border: "1px solid #334155", background: "transparent", color: "#94A3B8", cursor: "pointer", fontFamily: "inherit" }}
              >
                ← New trip
              </button>
            )}
            <span style={{ background: "#1E293B", color: "#64748B", fontSize: 9.5, padding: "3px 8px", borderRadius: 4, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              Prototype
            </span>
          </div>
        </div>

        {/* Content */}
        <div style={{ maxWidth: 640, margin: "0 auto", padding: "24px 18px 48px" }}>

          {/* Input card */}
          <div style={{ background: "#fff", borderRadius: 14, border: "1.5px solid #E2E8F0", padding: "20px", marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", letterSpacing: "0.07em", textTransform: "uppercase", marginBottom: 10 }}>
              Your trip
            </div>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={running}
              placeholder="Tell us where you want to go, when, who you're traveling with, your budget, and what kind of trip this is…"
              rows={4}
              style={{
                width: "100%",
                resize: "vertical",
                border: "1.5px solid #E2E8F0",
                borderRadius: 8,
                padding: "10px 12px",
                fontSize: 14,
                lineHeight: 1.65,
                color: "#1E293B",
                background: "#F8FAFC",
                fontFamily: "inherit",
                transition: "border-color 0.2s",
                opacity: running ? 0.6 : 1,
              }}
              onFocus={(e) => (e.target.style.borderColor = "#6366F1")}
              onBlur={(e) => (e.target.style.borderColor = "#E2E8F0")}
            />

            {/* Examples */}
            <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
              {EXAMPLES.map((ex, i) => (
                <button
                  key={i}
                  onClick={() => setInput(ex)}
                  disabled={running}
                  style={{ fontSize: 11.5, padding: "4px 10px", borderRadius: 20, border: "1px solid #E2E8F0", background: "#F8FAFC", color: "#64748B", cursor: "pointer", fontFamily: "inherit" }}
                >
                  Try example {i + 1}
                </button>
              ))}
            </div>

            <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end" }}>
              <button
                onClick={run}
                disabled={!canRun}
                style={{
                  background: canRun ? "#4F46E5" : "#E2E8F0",
                  color: canRun ? "#fff" : "#94A3B8",
                  border: "none",
                  borderRadius: 8,
                  padding: "10px 22px",
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: canRun ? "pointer" : "not-allowed",
                  fontFamily: "inherit",
                  letterSpacing: "-0.01em",
                  transition: "background 0.2s",
                }}
              >
                {running ? "Matching…" : "Match my trip →"}
              </button>
            </div>
          </div>

          {/* Pipeline */}
          {ran && (
            <div>
              <div style={{ fontSize: 10.5, fontWeight: 700, color: "#94A3B8", letterSpacing: "0.09em", textTransform: "uppercase", marginBottom: 10 }}>
                Pipeline
              </div>

              <Stage icon="🧭" agent="Scout" agentColor="#3B82F6" label="Input parsing" status={stages.a.status} data={stages.a.data} isText={false} />

              <div style={{ textAlign: "center", color: "#CBD5E1", fontSize: 16, margin: "5px 0", userSelect: "none" }}>↓</div>

              <Stage icon="⚡" agent="Meridian" agentColor="#8B5CF6" label="Destination matching" status={stages.b.status} data={stages.b.data} isText={false} />

              <div style={{ textAlign: "center", color: "#CBD5E1", fontSize: 16, margin: "5px 0", userSelect: "none" }}>↓</div>

              <Stage icon="🧭" agent="Scout" agentColor="#3B82F6" label="Your recommendation" status={stages.c.status} data={stages.c.data} isText={true} />
            </div>
          )}

          {/* Empty state */}
          {!ran && (
            <div style={{ textAlign: "center", padding: "44px 0" }}>
              <div style={{ fontSize: 38, marginBottom: 12 }}>🗺️</div>
              <p style={{ fontSize: 13, color: "#94A3B8", margin: 0 }}>
                Enter your trip above to see Scout → Meridian → Scout in action
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
