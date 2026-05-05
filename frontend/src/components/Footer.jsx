import React from 'react';

export default function Footer() {
  return (
    <footer className="w-full py-4 px-8 border-t border-surface-variant bg-surface-bright flex flex-col md:flex-row items-center justify-center gap-6 text-on-surface-variant text-sm font-medium">
      <div className="flex items-center gap-2 hover:text-primary transition-colors cursor-pointer" onClick={() => window.open('https://www.facebook.com/hth.hiu.', '_blank')}>
        <span className="material-symbols-outlined text-[20px]">facebook</span>
        <span>Facebook: hth.hiu</span>
      </div>
      <div className="flex items-center gap-2 hover:text-primary transition-colors">
        <span className="material-symbols-outlined text-[20px]">call</span>
        <span>Phone: 0904528538</span>
      </div>
    </footer>
  );
}
