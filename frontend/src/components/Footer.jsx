import React from 'react';

export default function Footer() {
  return (
    <footer className="w-full py-4 px-8 border-t border-surface-variant bg-surface-bright flex flex-col md:flex-row items-center justify-center gap-6 text-on-surface-variant text-sm font-medium">
      <div className="flex items-center gap-2 hover:text-primary transition-colors cursor-pointer" onClick={() => window.open('https://www.facebook.com/hth.hiu.', '_blank')}>
        <span className="inline-flex items-center justify-center text-[20px]" aria-hidden="true">
          <svg viewBox="0 0 24 24" className="h-[20px] w-[20px]" fill="currentColor" role="img" focusable="false" aria-hidden="true">
            <path d="M22 12c0-5.52-4.48-10-10-10S2 6.48 2 12c0 4.99 3.66 9.13 8.44 9.88v-6.99H7.9V12h2.54V9.8c0-2.5 1.5-3.9 3.78-3.9 1.09 0 2.23.2 2.23.2v2.46h-1.25c-1.23 0-1.61.77-1.61 1.56V12h2.74l-.44 2.89h-2.3v6.99C18.34 21.13 22 16.99 22 12z" />
          </svg>
        </span>
        <span>Facebook: hth.hiu</span>
      </div>
      <div className="flex items-center gap-2 hover:text-primary transition-colors">
        <span className="material-symbols-outlined text-[20px]">call</span>
        <span>Phone: 0904528538</span>
      </div>
    </footer>
  );
}
