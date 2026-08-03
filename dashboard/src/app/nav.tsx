'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import styles from './nav.module.css';

const ROUTES = [
  { href: '/', label: 'Dashboard', icon: '▦' },
  { href: '/projection', label: 'Proyección Económica', icon: '◈' },
] as const;

export default function Nav() {
  const pathname = usePathname() ?? '/';
  return (
    <nav className={styles.nav} aria-label="Navegación principal">
      <Link href="/" className={styles.brand} aria-label="Synthetic Trader — inicio">
        <span className={styles.brandMark}>
          <b>s</b>t
        </span>
        <span className={styles.brandName}>Synthetic Trader</span>
      </Link>
      <ul className={styles.list}>
        {ROUTES.map((route) => {
          const active =
            route.href === '/' ? pathname === '/' : pathname.startsWith(route.href);
          return (
            <li key={route.href}>
              <Link
                href={route.href}
                aria-current={active ? 'page' : undefined}
                className={`${styles.link} ${active ? styles.active : ''}`}
              >
                <span className={styles.icon} aria-hidden="true">
                  {route.icon}
                </span>
                <span className={styles.label}>{route.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
