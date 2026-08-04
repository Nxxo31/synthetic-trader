import { test, expect } from '@playwright/test';

/**
 * Responsive básico — viewport móvil (Pixel 5 / 393×851)
 * Verifica que las páginas no se rompen en pantallas pequeñas.
 * Este spec corre solo en el proyecto mobile-chrome (testMatch).
 */

test.describe('Responsive — vista móvil', () => {
  test('el dashboard principal renderiza sin scroll horizontal excesivo en móvil', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { level: 1, name: /Panel principal/i }),
    ).toBeVisible();
    // El body no debería tener scroll horizontal — el contenido cabe en el viewport.
    const scrollWidth = await page.evaluate(() => document.body.scrollWidth);
    const clientWidth = await page.evaluate(() => document.body.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 20); // tolerancia de 20px
  });

  test('los KPIs son visibles en vista móvil', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 3, name: 'Balance' })).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 3, name: 'Tasa de aciertos' }),
    ).toBeVisible();
  });

  test('la tabla de operaciones es scrollable horizontalmente en móvil', async ({ page }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    // La tabla tiene un contenedor con overflow-x-auto para scroll horizontal.
    const tableContainer = table.locator('xpath=ancestor::div[contains(@class,"overflow")][1]');
    await expect(tableContainer).toBeVisible();
  });

  test('la página de proyección renderiza en vista móvil', async ({ page }) => {
    await page.goto('/projection');
    await expect(
      page.getByRole('region', { name: /Asignador de capital/i }),
    ).toBeVisible();
    await expect(
      page.getByRole('region', { name: /Proyección y atribución/i }),
    ).toBeVisible();
  });

  test('los sliders del asignador son operables en móvil', async ({ page }) => {
    await page.goto('/projection');
    const slider = page.getByRole('slider', { name: /Reserva base/i });
    await expect(slider).toBeVisible();
    await expect(slider).toHaveValue('80');
  });

  test('los tabs de horizonte son visibles y seleccionables en móvil', async ({ page }) => {
    await page.goto('/projection');
    const tablist = page.getByRole('tablist', { name: /Horizonte temporal/i });
    await expect(tablist).toBeVisible();
    await page.getByRole('tab', { name: '30D' }).click();
    await expect(page.getByRole('tab', { name: '30D' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });
});
