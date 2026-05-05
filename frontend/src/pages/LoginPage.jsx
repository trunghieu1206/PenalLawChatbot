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
      <div className={`${styles.container} animate-fade-in`}>
        <div className={styles.logo}>
          <h1 className={styles.logoText}>VNPLaw</h1>
          <p className={styles.logoSub}>Legal Intelligence</p>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className="label" htmlFor="email">Email / Tên đăng nhập</label>
            <div className={styles.inputWrap}>
              <span className={`material-symbols-outlined ${styles.inputIcon}`}>person</span>
              <input
                id="email"
                type="text"
                autoComplete="username"
                className={`input ${styles.input}`}
                placeholder="Nhập thông tin của bạn"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
              />
            </div>
          </div>
          <div className={styles.field}>
            <label className="label" htmlFor="password">Mật khẩu</label>
            <div className={styles.inputWrap}>
              <span className={`material-symbols-outlined ${styles.inputIcon}`}>lock</span>
              <input
                id="password"
                type="password"
                className={`input ${styles.input}`}
                placeholder="Nhập mật khẩu"
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
              />
            </div>
          </div>

          <div className={styles.optionsRow}>
            <label className={styles.checkbox}>
              <input type="checkbox" />
              Ghi nhớ tôi
            </label>
            <button className={styles.link} type="button">Quên mật khẩu?</button>
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
