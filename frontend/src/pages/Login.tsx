import { FormEvent, useState } from "react";
import ThemeToggle from "../components/ThemeToggle";
import { supabase } from "../lib/supabase";
import { useTheme } from "../lib/theme";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [theme, toggleTheme] = useTheme();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setError(error.message);
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
        </div>
        <form onSubmit={onSubmit} className="card">
          <div className="brand" style={{ marginBottom: 20 }}>
            <span className="brand-dot" />
            Halal Trading Lab
          </div>
          <div className="field" style={{ marginBottom: 10 }}>
            <input type="email" placeholder="Email" value={email}
                   onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field" style={{ marginBottom: 16 }}>
            <input type="password" placeholder="Password" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <button type="submit" className="btn btn-primary" style={{ width: "100%" }}>
            Sign in
          </button>
          {error && <p role="alert" className="error-text" style={{ marginTop: 10 }}>{error}</p>}
        </form>
      </div>
    </div>
  );
}
