import { test, expect } from '@playwright/test';

/**
 * Navegación entre páginas / y /projection
 * Tests the global nav component — links, aria-current state, routing.
 */

test.describe('Navegación entre páginas', () => {
  test('el nav principal está visible en el dashboard', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    await expect(nav).toBeVisible();
    await expect(nav.getByRole('link', { name: /Dashboard/i })).toBeVisible();
    await expect(
      nav.getByRole('link', { name: /Proyección Económica/i }),
    ).toBeVisible();
  });

  test('el link Dashboard marca aria-current="page" en /', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    const dashboardLink = nav.getByRole('link', { name: /Dashboard/i });
    await expect(dashboardLink).toHaveAttribute('aria-current', 'page');
  });

  test('el link Proyección NO marca aria-current en /', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    const projectionLink = nav.getByRole('link', { name: /Proyección Económica/i });
    // On / this link should NOT be aria-current page.
    await expect(projectionLink).not.toHaveAttribute('aria-current', 'page');
  });

  test('al hacer clic en Proyección Económica navega a /projection', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    await nav.getByRole('link', { name: /Proyección Económica/i }).click();
    await expect(page).toHaveURL(/\/projection$/);
    await expect(
      page.getByRole('region', { name: /Asignador de capital/i }),
    ).toBeVisible();
  });

  test('en /projection el link Proyección marca aria-current="page"', async ({ page }) => {
    await page.goto('/projection');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    const projectionLink = nav.getByRole('link', { name: /Proyección Económica/i });
    await expect(projectionLink).toHaveAttribute('aria-current', 'page');
  });

  test('al hacer clic en Dashboard desde /projection vuelve a /', async ({ page }) => {
    await page.goto('/projection');
    const nav = page.getByRole('navigation', { name: /Navegación principal/i });
    await nav.getByRole('link', { name: /Dashboard/i }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByRole('heading', { level: 1, name: /Panel principal/i }),
    ).toBeVisible();
  });

  test('el brand link "Synthetic Trader — inicio" lleva a la raíz', async ({ page }) => {
    await page.goto('/projection');
    const brandLink = page.getByRole('link', { name: /Synthetic Trader — inicio/i });
    await brandLink.click();
    await expect(page).toHaveURL(/\/$/);
  });

  test('navegación ida y vuelta mantiene consistencia', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/$/);

    await page.getByRole('link', { name: /Proyección Económica/i }).click();
    await expect(page).toHaveURL(/\/projection$/);

    await page.getByRole('link', { name: /Dashboard/i }).click();
    await expect(page).toHaveURL(/\/$/);
  });
});
