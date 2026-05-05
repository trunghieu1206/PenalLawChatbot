import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';

export default function Sidebar({ activeTab }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const navItems = [
    { id: 'stats', label: 'Dashboard/Thống kê', icon: 'dashboard', path: '/stats' },
    { id: 'chat', label: 'Chat', icon: 'chat', path: '/chat' },
    { id: 'training', label: 'Chế độ Luyện tập', icon: 'gavel', path: '/training' },
  ];

  if (user?.role === 'admin') {
    navItems.push({ id: 'admin', label: 'Admin', icon: 'admin_panel_settings', path: '/admin' });
  }

  return (
    <nav className="h-screen w-64 border-r fixed left-0 top-0 border-slate-200 shadow-sm bg-slate-50 flex flex-col py-6 px-4 z-50">
      <div className="mb-8 px-2 flex items-center gap-3 cursor-pointer" onClick={() => navigate('/stats')}>
        <div className="w-8 h-8 rounded bg-primary text-on-primary flex items-center justify-center font-bold text-lg">V</div>
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight hover:text-primary transition-colors">VNPLaw</h1>
        </div>
      </div>
      
      <div className="flex-1 space-y-1">
        {navItems.map(item => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded font-semibold transition-colors duration-200 ease-in-out ${
                isActive
                  ? 'text-slate-900 border-r-4 border-slate-900 bg-slate-200'
                  : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
              }`}
            >
              <span className="material-symbols-outlined" style={isActive ? { fontVariationSettings: "'FILL' 1" } : {}}>{item.icon}</span>
              <span className="text-sm">{item.label}</span>
            </button>
          );
        })}
      </div>
      
      <div className="mt-auto space-y-1 pt-4 border-t border-slate-200">
        <button className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-200 ease-in-out">
          <span className="material-symbols-outlined">settings</span>
          <span className="text-sm">Cài đặt</span>
        </button>
        {user && (
          <button onClick={handleLogout} className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-error hover:bg-red-50 transition-colors duration-200 ease-in-out">
            <span className="material-symbols-outlined">logout</span>
            <span className="text-sm">Đăng xuất</span>
          </button>
        )}
      </div>
    </nav>
  );
}
