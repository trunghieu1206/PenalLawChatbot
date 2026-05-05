import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { authApi } from '../services/api.js';

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
    <div className="min-h-screen bg-surface flex flex-col justify-center items-center relative p-4">
      <button
        className="absolute top-8 left-8 flex items-center gap-2 text-on-surface-variant hover:text-on-surface transition-colors font-medium text-sm"
        onClick={handleBack}
        title="Quay lại trang chủ"
      >
        <span className="material-symbols-outlined text-lg">arrow_back</span>
        Quay lại
      </button>

      <div className="w-full max-w-md bg-surface-container-lowest border border-surface-variant rounded-2xl shadow-xl p-8 animate-fade-in">
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-primary text-on-primary flex items-center justify-center font-bold text-3xl mx-auto mb-4 shadow-sm">
            V
          </div>
          <h1 className="text-2xl font-bold text-on-surface tracking-tight mb-1">VNPLaw</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-sm font-semibold text-on-surface mb-1" htmlFor="email">Email / Tên đăng nhập</label>
            <div className="relative">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">person</span>
              <input
                id="email"
                type="text"
                autoComplete="username"
                className="w-full pl-10 pr-4 py-3 bg-surface-container-low border border-surface-variant rounded-lg text-sm text-on-surface focus:border-primary-container focus:ring-1 focus:ring-primary-container outline-none transition-all"
                placeholder="Nhập thông tin của bạn"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-semibold text-on-surface mb-1" htmlFor="password">Mật khẩu</label>
            <div className="relative">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">lock</span>
              <input
                id="password"
                type="password"
                className="w-full pl-10 pr-4 py-3 bg-surface-container-low border border-surface-variant rounded-lg text-sm text-on-surface focus:border-primary-container focus:ring-1 focus:ring-primary-container outline-none transition-all"
                placeholder="Nhập mật khẩu"
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
              />
            </div>
          </div>

          <div className="flex justify-between items-center text-sm">
            <label className="flex items-center gap-2 cursor-pointer text-on-surface-variant hover:text-on-surface transition-colors">
              <input type="checkbox" className="rounded border-surface-variant text-primary focus:ring-primary" />
              Ghi nhớ tôi
            </label>
            <button className="text-primary hover:underline font-medium" type="button">Quên mật khẩu?</button>
          </div>

          {error && <div className="p-3 bg-error-container text-on-error-container rounded-lg text-sm text-center">{error}</div>}

          <button 
            type="submit" 
            className="w-full bg-primary-container text-on-primary font-bold py-3 rounded-lg hover:bg-primary transition-all shadow-sm flex items-center justify-center disabled:opacity-70" 
            disabled={loading}
          >
            {loading ? <span className="material-symbols-outlined animate-spin">progress_activity</span> : 'Đăng nhập'}
          </button>
        </form>

        <p className="mt-8 text-center text-sm text-on-surface-variant">
          Chưa có tài khoản? <Link to="/register" className="text-primary font-semibold hover:underline">Đăng ký ngay</Link>
        </p>
      </div>
    </div>
  );
}
