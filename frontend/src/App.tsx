import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BriefcaseBusiness,
  Check,
  ChevronLeft,
  FileText,
  LockKeyhole,
  LogOut,
  Mail,
  MapPin,
  Pencil,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { api, ApiError } from "./api";
import type { Profile, ResumeProfile, RoleType, TargetField, User } from "./types";

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
  profile_completed: false,
});

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

function ProfileEditor({ initial, onSaved, onLogout }: { initial: Profile; onSaved: (profile: Profile) => void; onLogout: () => void }) {
  const [profile, setProfile] = useState(initial);
  const [step, setStep] = useState(initial.profile_completed ? 4 : 1);
  const [editing, setEditing] = useState(!initial.profile_completed);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
        mark_complete: markComplete,
      }) });
      setProfile(updated); onSaved(updated); setStep(4); setEditing(false);
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Could not save your profile."); }
    finally { setBusy(false); }
  }

  if (!editing && profile.profile_completed) {
    return (
      <div className="app-shell">
        <header className="app-header"><Brand /><div className="header-actions"><button onClick={() => { setEditing(true); setStep(1); }}><Pencil size={15} /> Edit profile</button><button onClick={onLogout}><LogOut size={15} /> Sign out</button></div></header>
        <main className="dashboard">
          <section className="welcome-block"><p className="eyebrow">Your signal is live</p><h1>We’ll take it from here, {profile.name.split(" ")[0]}.</h1><p>Your profile is matched against new roles throughout the day. One focused digest lands each morning.</p></section>
          <section className="status-grid">
            <article className="status-card accent"><span className="status-icon"><Mail /></span><div><small>Daily delivery</small><h3>{profile.email_digest_enabled ? "On · 8:00 AM" : "Paused"}</h3><p>{profile.email}</p></div></article>
            <article className="status-card"><span className="status-icon"><BriefcaseBusiness /></span><div><small>Opportunity types</small><h3>{profile.role_types.map((role) => role === "new_grad" ? "New grad" : "Internships").join(" + ")}</h3><p>{profile.target_fields.length} target field{profile.target_fields.length === 1 ? "" : "s"}</p></div></article>
            <article className="status-card"><span className="status-icon"><FileText /></span><div><small>Resume signal</small><h3>{profile.resume_profile.skills?.length ? `${profile.resume_profile.skills.length} skills found` : "Not added"}</h3><p>{profile.resume_profile.experience_level?.replace("_", " ") ?? "Add one anytime"}</p></div></article>
          </section>
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
  return <ProfileEditor initial={profile} onSaved={setProfile} onLogout={logout} />;
}
