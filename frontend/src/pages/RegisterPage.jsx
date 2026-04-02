import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { authApi } from '../services/api.js';
import styles from './Auth.module.css';

export default function RegisterPage() {
  const [form, setForm] = useState({ email: '', password: '', fullName: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleBack = () => navigate('/chat');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (form.password.length < 8) {
      setError('Mật khẩu phải có ít nhất 8 ký tự');
      return;
    }
    setLoading(true);
    try {
      const data = await authApi.register(form);
      login(data);
      navigate('/chat');
    } catch (err) {
      const errorMsg = err.response?.data?.message 
        || err.response?.data?.email 
        || err.response?.data?.error 
        || err.message 
        || 'Đăng ký thất bại';
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
          <span className={styles.logoIcon}>⚖️</span>
          <h1 className={styles.logoText}>LegalAI</h1>
          <p className={styles.logoSub}>Tạo tài khoản mới</p>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className="label" htmlFor="fullName">Họ và tên</label>
            <input id="fullName" type="text" className="input" placeholder="Nguyễn Văn A"
              value={form.fullName} onChange={e => setForm(f => ({ ...f, fullName: e.target.value }))} />
          </div>
          <div className={styles.field}>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" type="email" className="input" placeholder="example@email.com"
              value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} required />
          </div>
          <div className={styles.field}>
            <label className="label" htmlFor="password">Mật khẩu</label>
            <input id="password" type="password" className="input" placeholder="Ít nhất 8 ký tự"
              value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} required />
          </div>

          {error && <div className={styles.error}>{error}</div>}

          <button type="submit" className={`btn btn-primary ${styles.submitBtn}`} disabled={loading}>
            {loading ? <span className="loader" /> : 'Tạo tài khoản'}
          </button>
        </form>

        <p className={styles.footer}>
          Đã có tài khoản? <Link to="/login" className={styles.link}>Đăng nhập</Link>
        </p>
      </div>
    </div>
  );
}
