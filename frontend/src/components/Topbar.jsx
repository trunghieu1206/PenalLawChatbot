import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';

export default function Topbar() {
  const navigate = useNavigate();
  const { user } = useAuth();
  
  const userInitials = user?.fullName
    ? user.fullName.split(' ').slice(0, 2).map(part => part[0]).join('')
    : user?.email?.[0]?.toUpperCase() || 'G';

  return (
    <header className="bg-white/80 backdrop-blur-md fixed top-0 right-0 w-[calc(100%-16rem)] z-40 border-b border-surface-variant flex justify-between items-center h-16 px-8 transition-all duration-300">
      <div className="flex items-center gap-6">
        <span className="text-lg font-black text-slate-900 font-h3 hover:text-primary transition-colors cursor-pointer" onClick={() => navigate('/home')}>VNPLaw</span>
        <nav className="hidden md:flex gap-4">
          <button className="font-newsreader text-sm font-medium text-slate-500 hover:text-slate-900 transition-all duration-300">Tài liệu</button>
          <button className="font-newsreader text-sm font-medium text-slate-500 hover:text-slate-900 transition-all duration-300">Lưu trữ</button>
        </nav>
      </div>
      <div className="flex items-center gap-4">

        
        <div className="flex items-center gap-3 border-l border-surface-variant pl-4 ml-2">
          {user ? (
            <>
              <button className="text-on-surface-variant hover:text-primary transition-colors hidden md:block">
                <span className="material-symbols-outlined">notifications</span>
              </button>
              <div className="text-right hidden md:block">
                <div className="text-xs font-semibold text-on-surface-variant">{user.email}</div>
              </div>
              <button className="text-on-surface-variant w-8 h-8 rounded-full bg-surface-container border border-surface-variant flex items-center justify-center font-bold text-sm hover:text-primary transition-colors" title={user.email}>
                {userInitials}
              </button>
            </>
          ) : (
            <button onClick={() => navigate('/login')} className="bg-primary-container text-on-primary font-label-sm text-sm px-4 py-2 rounded hover:bg-primary transition-colors">
              Đăng nhập
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
