import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Bookmark,
  BriefcaseBusiness,
  Check,
  ChevronLeft,
  FileDown,
  FileText,
  ListFilter,
  LockKeyhole,
  LogOut,
  Mail,
  MapPin,
  Pencil,
  RefreshCw,
  Settings as SettingsIcon,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { api, apiBlob, ApiError } from "./api";
import type {
  MatchFilters,
  MatchItem,
  MatchListResponse,
  Profile,
  ResumeProfile,
  RoleType,
  TargetField,
  User,
} from "./types";

const FIELDS: Array<{ value: TargetField; label: string; detail: string }> = [
  { value: "software_engineering", label: "Software engineering", detail: "Web, mobile, systems, infrastructure" },
  { value: "data_science_analytics", label: "Data & analytics", detail: "Data science, BI, quantitative roles" },
  { value: "product_management", label: "Product management", detail: "APM and product strategy programs" },
  { value: "finance_investment_banking", label: "Finance & banking", detail: "Investment banking, markets, corporate finance" },
  { value: "consulting", label: "Consulting", detail: "Strategy, operations, and advisory" },
  { value: "marketing", label: "Marketing", detail: "Brand, growth, communications" },
  { value: "sales", label: "Sales", detail: "Business development and account roles" },
  { value: "operations", label: "Operations", detail: "Supply chain, programs, business operations" },
  { value: "design", label: "Design", detail: "Product, visual, and experience design" },
];

const emptyProfile = (user: User): Profile => ({
  name: user.name,
  email: user.email,
  role_types: [],
  target_fields: [],
  keywords: [],
  locations: [],
  sponsorship_required: null,
  freeform_notes: "",
  resume_profile: {},
  resume_updated_at: null,
  email_digest_enabled: true,
  email_digest_time: user.email_digest_time ?? "08:00",
  profile_completed: false,
});

function displayTime(value: string): string {
  const [hourText, minute = "00"] = value.split(":");
  const hour = Number(hourText);
  const period = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return `${displayHour}:${minute} ${period}`;
}

function Brand() {
  return (
    <div className="brand" aria-label="Job Signal">
      <span className="brand-mark"><Sparkles size={17} strokeWidth={2.2} /></span>
      <span>Job Signal</span>
    </div>
  );
}

function AuthPage({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    try {
      const user = await api<User>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(
          mode === "signup"
            ? { ...payload, consent: form.get("consent") === "on", phone: payload.phone || null }
            : { email: payload.email, password: payload.password },
        ),
      });
      onAuthenticated(user);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not continue.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-story">
        <Brand />
        <div className="story-copy">
          <p className="eyebrow">A quieter way to job hunt</p>
          <h1>Good opportunities,<br />already sorted.</h1>
          <p className="lede">
            Tell us where you’re headed. We scan early-career programs across industries,
            rank what fits, and send one useful email a day.
          </p>
        </div>
        <div className="signal-card" aria-hidden="true">
          <div className="signal-top"><span>Tomorrow’s signal</span><span>8:00 AM</span></div>
          <div className="signal-role">
            <span className="company-token">BC</span>
            <div><strong>2027 Analyst</strong><span>Bain Capital · Boston</span></div>
            <span className="fit">94% fit</span>
          </div>
          <div className="signal-role muted">
            <span className="company-token amber">P&G</span>
            <div><strong>Marketing Graduate</strong><span>P&G · Cincinnati</span></div>
            <span className="fit">89% fit</span>
          </div>
        </div>
        <p className="privacy-note"><LockKeyhole size={14} /> Your resume becomes matching signals—not a stored file.</p>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <p className="eyebrow">{mode === "signup" ? "Create your profile" : "Welcome back"}</p>
          <h2>{mode === "signup" ? "Start with the essentials." : "Pick up where you left off."}</h2>
          <p className="subtle">{mode === "signup" ? "Setup takes about three minutes." : "Your next digest is waiting."}</p>
          <form onSubmit={submit} className="stack-form">
            {mode === "signup" && (
              <label>Full name<input name="name" autoComplete="name" required maxLength={120} placeholder="Alex Morgan" /></label>
            )}
            <label>Email<input name="email" type="email" autoComplete="email" required placeholder="alex@example.com" /></label>
            <label>Password<input name="password" type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} minLength={mode === "signup" ? 10 : 1} required placeholder="At least 10 characters" /></label>
            {mode === "signup" && (
              <>
                <label>Phone <span className="optional">optional, for SMS later</span><input name="phone" type="tel" autoComplete="tel" placeholder="+15551234567" /></label>
                <label className="consent-row"><input name="consent" type="checkbox" required /><span>I agree to receive the daily job digest and accept the privacy terms.</span></label>
              </>
            )}
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="primary-button" disabled={busy}>{busy ? "Working…" : mode === "signup" ? "Build my job signal" : "Sign in"}<ArrowRight size={17} /></button>
          </form>
          <button className="text-button" onClick={() => { setMode(mode === "signup" ? "login" : "signup"); setError(""); }}>
            {mode === "signup" ? "Already have a profile? Sign in" : "New here? Create a profile"}
          </button>
        </div>
      </section>
    </main>
  );
}

function ChoiceCard({ selected, title, detail, onClick }: { selected: boolean; title: string; detail: string; onClick: () => void }) {
  return (
    <button type="button" className={`choice-card ${selected ? "selected" : ""}`} onClick={onClick} aria-pressed={selected}>
      <span><strong>{title}</strong><small>{detail}</small></span>
      <span className="check-circle">{selected && <Check size={14} strokeWidth={3} />}</span>
    </button>
  );
}

function TokenInput({ label, hint, values, onChange, placeholder }: { label: string; hint: string; values: string[]; onChange: (values: string[]) => void; placeholder: string }) {
  const [draft, setDraft] = useState("");
  function add() {
    const candidates = draft.split(",").map((item) => item.trim()).filter(Boolean);
    if (candidates.length) onChange([...new Set([...values, ...candidates])]);
    setDraft("");
  }
  return (
    <label>{label}<span className="field-hint">{hint}</span>
      <div className="token-box">
        {values.map((value) => <button type="button" className="token" key={value} onClick={() => onChange(values.filter((item) => item !== value))}>{value}<span>×</span></button>)}
        <input value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={add} onKeyDown={(event) => { if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(); } }} placeholder={values.length ? "Add another" : placeholder} />
      </div>
    </label>
  );
}

function ResumePanel({ profile, onUploaded, onApplyFields }: { profile: Profile; onUploaded: (resume: ResumeProfile, updatedAt: string) => void; onApplyFields: (fields: TargetField[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true); setError("");
    const form = new FormData(); form.append("file", file);
    try {
      const result = await api<{ resume_profile: ResumeProfile; resume_updated_at: string }>("/profile/resume", { method: "POST", body: form });
      onUploaded(result.resume_profile, result.resume_updated_at);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not analyze that resume.");
    } finally { setBusy(false); if (input.current) input.current.value = ""; }
  }
  const resume = profile.resume_profile;
  const hasResume = Boolean(resume.skills?.length || resume.summary);
  return (
    <div className="resume-wrap">
      <input ref={input} className="visually-hidden" type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => upload(event.target.files?.[0])} />
      <button type="button" className={`upload-zone ${hasResume ? "compact" : ""}`} onClick={() => input.current?.click()} disabled={busy}>
        <span className="upload-icon">{busy ? <Sparkles className="spin" /> : <UploadCloud />}</span>
        <span><strong>{busy ? "Finding your strongest signals…" : hasResume ? "Replace resume" : "Upload your resume"}</strong><small>PDF or DOCX · 5 MB max · never stored as a file</small></span>
      </button>
      {error && <p className="form-error" role="alert">{error}</p>}
      {hasResume && (
        <div className="extraction-card">
          <div className="extraction-heading"><span><Sparkles size={17} /> Extracted signal</span><small>{profile.resume_updated_at ? new Date(profile.resume_updated_at).toLocaleDateString() : "Just now"}</small></div>
          {resume.summary && <p>{resume.summary}</p>}
          {resume.skills && <div className="skill-list">{resume.skills.slice(0, 12).map((skill) => <span key={skill}>{skill}</span>)}</div>}
          {!!resume.inferred_target_fields?.length && (
            <div className="suggestion-row"><span>Suggested paths: {resume.inferred_target_fields.map((field) => FIELDS.find((item) => item.value === field)?.label).join(", ")}</span><button type="button" onClick={() => onApplyFields(resume.inferred_target_fields ?? [])}>Add to profile</button></div>
          )}
        </div>
      )}
    </div>
  );
}

// The API caps a page at 200 and defaults to 50. Requesting a page
// explicitly (rather than relying on that default) is what makes "Show more"
// able to reach past the first page at all — without it the list silently
// stopped at 50 while the header still counted every match.
const PAGE_SIZE = 25;

function buildMatchQuery(filters: MatchFilters, offset: number): string {
  const params = new URLSearchParams();
  if (filters.company) params.set("company", filters.company);
  if (filters.location) params.set("location", filters.location);
  if (filters.target_field) params.set("target_field", filters.target_field);
  if (filters.priority) params.set("priority", filters.priority);
  if (filters.min_score !== undefined) params.set("min_score", String(filters.min_score));
  if (filters.saved) params.set("saved", "true");
  if (filters.new_only) params.set("new_only", "true");
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  return `?${params.toString()}`;
}

function MatchesPage({ onBack, onLogout }: { onBack: () => void; onLogout: () => void }) {
  const [items, setItems] = useState<MatchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<MatchFilters>({});
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [tailoring, setTailoring] = useState(false);
  const [tailorError, setTailorError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // Guards against an out-of-order response overwriting a newer one: two
  // loads can be in flight at once (blurring a text field while changing a
  // select fires both), and the slower request must not win.
  const requestId = useRef(0);

  async function load(withFilters: MatchFilters = filters, { append = false } = {}) {
    const ticket = ++requestId.current;
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError("");
    try {
      const offset = append ? items.length : 0;
      const result = await api<MatchListResponse>(`/matches${buildMatchQuery(withFilters, offset)}`);
      if (ticket !== requestId.current) return;
      setItems((current) => (append ? [...current, ...result.items] : result.items));
      setTotal(result.total);
    } catch (caught) {
      if (ticket !== requestId.current) return;
      setError(caught instanceof ApiError ? caught.message : "Could not load matches.");
    } finally {
      if (ticket === requestId.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }

  // Text filters (company/location) are applied on blur/Enter below, not on
  // every keystroke; select/checkbox filters (including the initial load)
  // are immediate since there's no typing to debounce.
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.target_field, filters.priority, filters.saved, filters.new_only]);

  // Selections are only meaningful for rows the user can still see. Keeping
  // them across a filter change left the tailor bar offering to generate
  // resumes for jobs that had scrolled out of the result set entirely.
  useEffect(() => {
    setSelected((current) => {
      const visible = new Set(items.map((item) => item.id));
      const kept = [...current].filter((id) => visible.has(id));
      return kept.length === current.size ? current : new Set(kept);
    });
  }, [items]);

  function toggleSelected(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function toggleSaved(item: MatchItem) {
    const nextSaved = !item.saved;
    setItems((current) => current.map((row) => (row.id === item.id ? { ...row, saved: nextSaved } : row)));
    try {
      await api(`/matches/${item.id}/save`, { method: "POST", body: JSON.stringify({ saved: nextSaved }) });
    } catch {
      // Roll back on failure — the optimistic flip above didn't stick server-side.
      setItems((current) => current.map((row) => (row.id === item.id ? { ...row, saved: item.saved } : row)));
    }
  }

  async function tailorSelected(file?: File) {
    if (!file || selected.size === 0) return;
    setTailoring(true);
    setTailorError("");
    try {
      const form = new FormData();
      form.append("file", file);
      // MatchItem.id is a *match* id, which is the only id this list holds —
      // the endpoint selects on it directly (see api/resume_tailor.py).
      selected.forEach((id) => form.append("match_ids", String(id)));
      const { blob, filename } = await apiBlob("/resume/tailor", { method: "POST", body: form });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      // Firefox only acts on a click if the anchor is in the document, and
      // revoking the object URL synchronously cancels the download there and
      // in Safari — so attach it, click, then clean up on the next tick.
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      setTimeout(() => {
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
      }, 0);
    } catch (caught) {
      setTailorError(
        caught instanceof ApiError ? caught.message : "Could not tailor a resume for those postings.",
      );
    } finally {
      setTailoring(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  const hasFilters = Boolean(
    filters.company || filters.location || filters.target_field || filters.priority ||
    filters.saved || filters.new_only,
  );

  return (
    <div className="app-shell">
      <header className="app-header">
        <Brand />
        <div className="header-actions">
          <button onClick={onBack}><ChevronLeft size={15} /> Dashboard</button>
          <button onClick={onLogout}><LogOut size={15} /> Sign out</button>
        </div>
      </header>
      <main className="matches-page">
      <section className="matches-heading">
        <div>
          <p className="eyebrow">Your matches</p>
          <h1>Everything ranked for you.</h1>
          {/* "on file" would be wrong once a filter narrows the set, since
              `total` is the count of what matched the filter, not of
              everything the user has. */}
          <p className="subtle">
            {hasFilters
              ? `${total} match${total === 1 ? "" : "es"} for these filters.`
              : `${total} match${total === 1 ? "" : "es"} on file.`}
          </p>
        </div>
      </section>

      <section className="matches-filters">
        <span className="filters-label"><ListFilter size={14} /> Filters</span>
        <input
          placeholder="Company"
          value={filters.company ?? ""}
          onChange={(event) => setFilters({ ...filters, company: event.target.value || undefined })}
          onBlur={() => load()}
          onKeyDown={(event) => event.key === "Enter" && load()}
        />
        <input
          placeholder="Location"
          value={filters.location ?? ""}
          onChange={(event) => setFilters({ ...filters, location: event.target.value || undefined })}
          onBlur={() => load()}
          onKeyDown={(event) => event.key === "Enter" && load()}
        />
        <select
          value={filters.target_field ?? ""}
          onChange={(event) =>
            setFilters({ ...filters, target_field: (event.target.value || undefined) as TargetField | undefined })
          }
        >
          <option value="">All fields</option>
          {FIELDS.map((field) => (
            <option key={field.value} value={field.value}>{field.label}</option>
          ))}
        </select>
        <select
          value={filters.priority ?? ""}
          onChange={(event) =>
            setFilters({ ...filters, priority: (event.target.value || undefined) as MatchFilters["priority"] })
          }
        >
          <option value="">Any priority</option>
          <option value="high">High fit</option>
          <option value="normal">Normal</option>
        </select>
        <label className="filter-toggle">
          <input
            type="checkbox"
            checked={!!filters.saved}
            onChange={(event) => setFilters({ ...filters, saved: event.target.checked || undefined })}
          /> Saved only
        </label>
        <label className="filter-toggle">
          <input
            type="checkbox"
            checked={!!filters.new_only}
            onChange={(event) => setFilters({ ...filters, new_only: event.target.checked || undefined })}
          /> New since last visit
        </label>
      </section>

      {selected.size > 0 && (
        <section className="tailor-bar">
          <span><FileDown size={16} /> Tailor a resume for {selected.size} selected job{selected.size === 1 ? "" : "s"}</span>
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(event) => tailorSelected(event.target.files?.[0])}
          />
          <button className="primary-button narrow" disabled={tailoring} onClick={() => fileInput.current?.click()}>
            {tailoring ? "Tailoring…" : "Upload resume & generate"}
          </button>
        </section>
      )}
      {tailorError && <p className="form-error" role="alert">{tailorError}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}

      {loading ? (
        <p className="subtle">Loading matches…</p>
      ) : items.length === 0 ? (
        <p className="subtle">No matches yet for these filters.</p>
      ) : (
        <ul className="match-list">
          {items.map((item) => (
            <li className={`match-row ${item.is_new ? "is-new" : ""}`} key={item.id}>
              <input
                type="checkbox"
                checked={selected.has(item.id)}
                onChange={() => toggleSelected(item.id)}
                aria-label={`Select ${item.company} ${item.title}`}
              />
              <div className="match-main">
                <a href={item.url} target="_blank" rel="noreferrer"><strong>{item.title}</strong></a>
                <span className="match-meta">{item.company}{item.location ? ` · ${item.location}` : ""}</span>
                {item.blurb && <p className="match-blurb">{item.blurb}</p>}
              </div>
              <div className="match-tags">
                {item.priority === "high" && <span className="summary-pill high">High fit</span>}
                {item.matched_target_field && (
                  <span className="summary-pill">{FIELDS.find((field) => field.value === item.matched_target_field)?.label}</span>
                )}
                {item.is_new && <span className="summary-pill new">New</span>}
              </div>
              <button
                className={`save-toggle ${item.saved ? "saved" : ""}`}
                onClick={() => toggleSaved(item)}
                aria-label={item.saved ? "Remove from saved" : "Save this match"}
              >
                <Bookmark size={16} fill={item.saved ? "currentColor" : "none"} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && items.length < total && (
        <div className="match-more">
          <button className="secondary-button" disabled={loadingMore} onClick={() => load(filters, { append: true })}>
            {loadingMore ? "Loading…" : `Show more (${total - items.length} left)`}
          </button>
        </div>
      )}
      </main>
    </div>
  );
}

function ProfileEditor({
  initial,
  onSaved,
  onLogout,
  initialView,
}: {
  initial: Profile;
  onSaved: (profile: Profile) => void;
  onLogout: () => void;
  initialView?: string | null;
}) {
  const [profile, setProfile] = useState(initial);
  const [step, setStep] = useState(initial.profile_completed ? 4 : 1);
  const [editing, setEditing] = useState(!initial.profile_completed);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);
  const [resendMessage, setResendMessage] = useState<{ ok: boolean; text: string } | null>(null);
  // Lets notification emails deep-link straight to the matches view (e.g.
  // "?view=matches") even though this app has no client-side router — see
  // notify/dispatch.py::_matches_url on the backend.
  const [matchesOpen, setMatchesOpen] = useState(initialView === "matches" && initial.profile_completed);

  function closeMatches() {
    setMatchesOpen(false);
    // Drop the deep-link parameter on the way out, or a refresh from the
    // dashboard would bounce straight back to the matches view.
    const url = new URL(window.location.href);
    if (url.searchParams.has("view")) {
      url.searchParams.delete("view");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  }

  const progress = useMemo(() => (step >= 4 ? 100 : Math.round((step / 3) * 100)), [step]);
  function toggleField(value: TargetField) {
    setProfile((current) => ({ ...current, target_fields: current.target_fields.includes(value) ? current.target_fields.filter((item) => item !== value) : [...current.target_fields, value] }));
  }
  function toggleRole(value: RoleType) {
    setProfile((current) => ({ ...current, role_types: current.role_types.includes(value) ? current.role_types.filter((item) => item !== value) : [...current.role_types, value] }));
  }
  async function save(markComplete: boolean) {
    setBusy(true); setError("");
    try {
      const updated = await api<Profile>("/profile", { method: "PUT", body: JSON.stringify({
        name: profile.name,
        role_types: profile.role_types,
        target_fields: profile.target_fields,
        keywords: profile.keywords,
        locations: profile.locations,
        sponsorship_required: profile.sponsorship_required,
        freeform_notes: profile.freeform_notes,
        email_digest_enabled: profile.email_digest_enabled,
        email_digest_time: profile.email_digest_time,
        mark_complete: markComplete,
      }) });
      setProfile(updated); onSaved(updated); setStep(4); setEditing(false);
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Could not save your profile."); }
    finally { setBusy(false); }
  }

  async function saveSettings() {
    setBusy(true); setError(""); setSettingsSaved(false);
    try {
      const updated = await api<Profile>("/profile/settings", { method: "PUT", body: JSON.stringify({
        email_digest_enabled: profile.email_digest_enabled,
        email_digest_time: profile.email_digest_time,
      }) });
      setProfile(updated); onSaved(updated); setSettingsSaved(true);
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Could not save your settings."); }
    finally { setBusy(false); }
  }

  async function resendEmail() {
    setResendBusy(true);
    setResendMessage(null);
    try {
      const result = await api<{ sent: boolean; match_count: number }>("/matches/resend-email", {
        method: "POST",
      });
      setResendMessage({
        ok: true,
        text:
          result.match_count > 0
            ? `Sent — ${result.match_count} match${result.match_count === 1 ? "" : "es"}.`
            : "Sent — no matches yet, but check your inbox.",
      });
    } catch (caught) {
      setResendMessage({
        ok: false,
        text: caught instanceof ApiError ? caught.message : "Could not send the email.",
      });
    } finally {
      setResendBusy(false);
    }
  }

  if (matchesOpen && profile.profile_completed) {
    return <MatchesPage onBack={closeMatches} onLogout={onLogout} />;
  }

  if (settingsOpen && profile.profile_completed) {
    return (
      <div className="app-shell">
        <header className="app-header"><Brand /><div className="header-actions"><button onClick={() => { setSettingsOpen(false); setError(""); setSettingsSaved(false); }}><ChevronLeft size={15} /> Dashboard</button><button onClick={onLogout}><LogOut size={15} /> Sign out</button></div></header>
        <main className="settings-page">
          <section className="settings-heading"><p className="eyebrow">Settings</p><h1>Make the signal fit your day.</h1><p>Choose when your daily digest arrives. Delivery times use Eastern Time.</p></section>
          <section className="settings-card">
            <div className="settings-row"><div><h2>Daily email digest</h2><p>Receive one message with your newest matches—or a short update on quiet days.</p></div><label className="switch-control"><input aria-label="Enable daily email digest" type="checkbox" checked={profile.email_digest_enabled} onChange={(event) => { setProfile({ ...profile, email_digest_enabled: event.target.checked }); setSettingsSaved(false); }} /><span /></label></div>
            <label className="delivery-time">Delivery time <span>Eastern Time</span><input type="time" step="60" value={profile.email_digest_time} disabled={!profile.email_digest_enabled} onChange={(event) => { setProfile({ ...profile, email_digest_time: event.target.value }); setSettingsSaved(false); }} /></label>
            <div className="settings-preview"><Mail size={18} /><span><small>Next daily window</small><strong>{profile.email_digest_enabled ? displayTime(profile.email_digest_time) : "Digest paused"}</strong></span></div>
            {error && <p className="form-error" role="alert">{error}</p>}
            {settingsSaved && <p className="form-success" role="status">Settings saved.</p>}
            <button className="primary-button settings-save" disabled={busy || !profile.email_digest_time} onClick={saveSettings}>{busy ? "Saving…" : "Save settings"}</button>
          </section>
        </main>
      </div>
    );
  }

  if (!editing && profile.profile_completed) {
    return (
      <div className="app-shell">
        <header className="app-header"><Brand /><div className="header-actions"><button onClick={() => setMatchesOpen(true)}><ListFilter size={15} /> Matches</button><button onClick={() => { setSettingsOpen(true); setSettingsSaved(false); }}><SettingsIcon size={15} /> Settings</button><button onClick={() => { setEditing(true); setStep(1); }}><Pencil size={15} /> Edit profile</button><button onClick={onLogout}><LogOut size={15} /> Sign out</button></div></header>
        <main className="dashboard">
          <section className="welcome-block"><p className="eyebrow">Your signal is live</p><h1>We’ll take it from here, {profile.name.split(" ")[0]}.</h1><p>Your profile is matched against new roles throughout the day. One focused digest lands each morning.</p></section>
          <section className="status-grid">
            <article className="status-card accent">
              <span className="status-icon"><Mail /></span>
              <div>
                <small>Daily delivery</small>
                <h3>{profile.email_digest_enabled ? `On · ${displayTime(profile.email_digest_time)}` : "Paused"}</h3>
                <p>{profile.email}</p>
                <button type="button" className="resend-button" disabled={resendBusy || !profile.email} onClick={resendEmail}>
                  <RefreshCw size={13} className={resendBusy ? "spin" : undefined} /> {resendBusy ? "Sending…" : "Resend email now"}
                </button>
              </div>
            </article>
            <article className="status-card"><span className="status-icon"><BriefcaseBusiness /></span><div><small>Opportunity types</small><h3>{profile.role_types.map((role) => role === "new_grad" ? "New grad" : "Internships").join(" + ")}</h3><p>{profile.target_fields.length} target field{profile.target_fields.length === 1 ? "" : "s"}</p></div></article>
            <article className="status-card"><span className="status-icon"><FileText /></span><div><small>Resume signal</small><h3>{profile.resume_profile.skills?.length ? `${profile.resume_profile.skills.length} skills found` : "Not added"}</h3><p>{profile.resume_profile.experience_level?.replace("_", " ") ?? "Add one anytime"}</p></div></article>
          </section>
          {resendMessage && (
            <p className={resendMessage.ok ? "form-success" : "form-error"} role="status">{resendMessage.text}</p>
          )}
          <section className="profile-summary">
            <div className="summary-heading"><div><p className="eyebrow">Matching profile</p><h2>What we’re looking for</h2></div><button className="secondary-button" onClick={() => { setEditing(true); setStep(1); }}><Pencil size={15} /> Adjust</button></div>
            <div className="summary-columns"><div><h4>Fields</h4>{profile.target_fields.map((field) => <span className="summary-pill" key={field}>{FIELDS.find((item) => item.value === field)?.label}</span>)}</div><div><h4>Locations</h4><p><MapPin size={15} /> {profile.locations.length ? profile.locations.join(" · ") : "Anywhere"}</p></div><div><h4>Keywords</h4><p>{profile.keywords.length ? profile.keywords.join(" · ") : "Resume and field signals"}</p></div></div>
          </section>
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell onboarding-bg">
      <header className="app-header"><Brand /><button className="quiet-button" onClick={onLogout}><LogOut size={15} /> Sign out</button></header>
      <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
      <main className="onboarding">
        <aside className="step-rail"><p className="eyebrow">Your profile</p>{["Direction", "Preferences", "Resume signal"].map((label, index) => <div className={`step-label ${step === index + 1 ? "active" : ""} ${step > index + 1 ? "done" : ""}`} key={label}><span>{step > index + 1 ? <Check size={13} /> : index + 1}</span>{label}</div>)}</aside>
        <section className="form-card">
          {step === 1 && <><p className="eyebrow">Step 1 of 3</p><h1>Where do you want to go?</h1><p className="subtle wide">Choose as many paths as feel true. The ranking model uses these as your primary direction—not a hard box.</p><div className="choice-grid">{FIELDS.map((field) => <ChoiceCard key={field.value} selected={profile.target_fields.includes(field.value)} title={field.label} detail={field.detail} onClick={() => toggleField(field.value)} />)}</div><div className="section-divider" /><h3>Which opportunities?</h3><div className="role-row"><ChoiceCard selected={profile.role_types.includes("new_grad")} title="New graduate" detail="Full-time entry-level roles and programs" onClick={() => toggleRole("new_grad")} /><ChoiceCard selected={profile.role_types.includes("intern")} title="Internship & co-op" detail="Summer, semester, and rotational placements" onClick={() => toggleRole("intern")} /></div></>}
          {step === 2 && <><p className="eyebrow">Step 2 of 3</p><h1>Shape the search.</h1><p className="subtle wide">A few thoughtful constraints beat a wall of filters. Leave anything open if you’re flexible.</p><div className="preference-stack"><TokenInput label="Preferred locations" hint="Cities, states, countries, or Remote" values={profile.locations} onChange={(locations) => setProfile({ ...profile, locations })} placeholder="New York, Remote" /><TokenInput label="Keywords" hint="Skills, teams, industries, or programs" values={profile.keywords} onChange={(keywords) => setProfile({ ...profile, keywords })} placeholder="Python, consumer, rotational" /><fieldset><legend>Will you require employment sponsorship?</legend><div className="radio-row">{[[null, "Not sure"], [true, "Yes"], [false, "No"]].map(([value, label]) => <button type="button" className={profile.sponsorship_required === value ? "active" : ""} key={label as string} onClick={() => setProfile({ ...profile, sponsorship_required: value as boolean | null })}>{label as string}</button>)}</div></fieldset><label>Anything else?<span className="field-hint">Use plain language—we’ll include it in ranking.</span><textarea rows={4} maxLength={2000} value={profile.freeform_notes} onChange={(event) => setProfile({ ...profile, freeform_notes: event.target.value })} placeholder="I’m especially interested in mission-driven teams and rotational programs…" /></label></div></>}
          {step === 3 && <><p className="eyebrow">Step 3 of 3</p><h1>Add your experience signal.</h1><p className="subtle wide">Optional, but useful. We extract only compact skills and career signals. The original file and full text are discarded after analysis.</p><ResumePanel profile={profile} onUploaded={(resume_profile, resume_updated_at) => setProfile({ ...profile, resume_profile, resume_updated_at })} onApplyFields={(fields) => setProfile((current) => ({ ...current, target_fields: [...new Set([...current.target_fields, ...fields])] }))} /><label className="email-toggle"><span><Mail size={19} /><span><strong>Daily email digest</strong><small>One email each morning when new matches are waiting.</small></span></span><input type="checkbox" checked={profile.email_digest_enabled} onChange={(event) => setProfile({ ...profile, email_digest_enabled: event.target.checked })} /></label></>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <footer className="form-footer">{step > 1 ? <button className="back-button" onClick={() => setStep(step - 1)}><ChevronLeft size={17} /> Back</button> : <span />}{step < 3 ? <button className="primary-button narrow" disabled={(step === 1 && (!profile.target_fields.length || !profile.role_types.length))} onClick={() => setStep(step + 1)}>Continue <ArrowRight size={17} /></button> : <button className="primary-button narrow" disabled={busy} onClick={() => save(true)}>{busy ? "Saving…" : "Turn on my signal"} <ArrowRight size={17} /></button>}</footer>
        </section>
      </main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadProfile(activeUser: User) {
    try { setProfile(await api<Profile>("/profile")); }
    catch { setProfile(emptyProfile(activeUser)); }
  }
  useEffect(() => {
    api<User>("/auth/me").then(async (current) => { setUser(current); await loadProfile(current); }).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);
  async function authenticated(current: User) { setUser(current); await loadProfile(current); }
  async function logout() { await api<void>("/auth/logout", { method: "POST" }); setUser(null); setProfile(null); }

  if (loading) return <div className="loading-screen"><Brand /><span>Finding your signal…</span></div>;
  if (!user) return <AuthPage onAuthenticated={authenticated} />;
  if (!profile) return <div className="loading-screen"><Brand /><span>Opening your profile…</span></div>;
  const initialView = new URLSearchParams(window.location.search).get("view");
  return <ProfileEditor initial={profile} onSaved={setProfile} onLogout={logout} initialView={initialView} />;
}
