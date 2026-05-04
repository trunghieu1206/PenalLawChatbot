import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { authApi } from '../services/api.js';
import styles from './Auth.module.css';

export default function LoginPage() {
  const [form, setForm] = useState({ email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleBack = () => navigate('/chat');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await authApi.login(form);
      login(data);
      navigate('/chat');
    } catch (err) {
      const errorMsg = err.response?.data?.message 
        || err.response?.data?.error 
        || err.message 
        || 'Đăng nhập thất bại';
      setError(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.bg} />
      <button
        className={styles.backBtn}
        onClick={handleBack}
        title="Quay lại trang chủ"
      >
        ← Quay lại
      </button>
      <div className={`${styles.container} card animate-fade-in`}>
        <div className={styles.logo}>
          <h1 className={styles.logoText}>VNPLaw</h1>
          <p className={styles.logoSub}>Hệ Thống Tư Vấn Pháp Luật Hình Sự Việt Nam</p>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className="label" htmlFor="email">Email / Tên đăng nhập</label>
            <input
              id="email"
              type="text"
              autoComplete="username"
              className="input"
              placeholder="email@example.com hoặc tên đăng nhập"
              value={form.email}
              onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
              required
            />
          </div>
          <div className={styles.field}>
            <label className="label" htmlFor="password">Mật khẩu</label>
            <input
              id="password"
              type="password"
              className="input"
              placeholder="••••••••"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              required
            />
          </div>

          {error && <div className={styles.error}>{error}</div>}

          <button type="submit" className={`btn btn-primary ${styles.submitBtn}`} disabled={loading}>
            {loading ? <span className="loader" /> : 'Đăng nhập'}
          </button>
        </form>

        <p className={styles.footer}>
          Chưa có tài khoản? <Link to="/register" className={styles.link}>Đăng ký ngay</Link>
        </p>

      </div>
    </div>
  );
}
