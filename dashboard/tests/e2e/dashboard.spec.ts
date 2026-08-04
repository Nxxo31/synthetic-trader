import { test, expect } from '@playwright/test';

/**
 * Dashboard principal (/) — E2E tests
 * Tests run against the live Next.js app + FastAPI backend.
 * Each test exercises one behavior; no shared state between tests.
 */

test.describe('Dashboard — página principal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('muestra el título principal del panel', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 1, name: /Panel principal/i }),
    ).toBeVisible();
  });

  test('muestra el símbolo y modo de operación', async ({ page }) => {
    // The symbol/mode line renders as static text after the h1.
    const info = page.getByText(/Símbolo:.*Modo:/i);
    await expect(info).toBeVisible();
  });

  test('renderiza los 4 KPIs principales', async ({ page }) => {
    // KPI labels are h3 headings rendered from the kpis array.
    await expect(page.getByRole('heading', { level: 3, name: 'Balance' })).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 3, name: 'Resultado de operaciones' }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 3, name: 'Tasa de aciertos' }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 3, name: /Índice de rendimiento \(Sharpe\)/i }),
    ).toBeVisible();
  });

  test('el KPI de Balance muestra un valor monetario válido', async ({ page }) => {
    // The Balance value is a <p> following the h3 "Balance".
    const balanceCard = page.locator('div', { has: page.getByRole('heading', { name: 'Balance' }) });
    const value = balanceCard.locator('p').first();
    await expect(value).toBeVisible();
    await expect(value).toContainText(/\$/);
  });

  test('el KPI de Resultado de operaciones muestra un valor', async ({ page }) => {
    const pnlCard = page.locator('div', {
      has: page.getByRole('heading', { name: 'Resultado de operaciones' }),
    });
    await expect(pnlCard.locator('p').first()).toContainText(/\$/);
  });

  test('el KPI de Tasa de aciertos muestra un porcentaje', async ({ page }) => {
    const wrCard = page.locator('div', {
      has: page.getByRole('heading', { name: 'Tasa de aciertos' }),
    });
    await expect(wrCard.locator('p').first()).toContainText(/%/);
  });

  test('el KPI de Sharpe muestra un número decimal', async ({ page }) => {
    const sharpeCard = page.locator('div', {
      has: page.getByRole('heading', { name: /Índice de rendimiento/i }),
    });
    const value = sharpeCard.locator('p').first();
    await expect(value).toBeVisible();
    // Sharpe is rendered as a fixed 2-decimal number (e.g. "1.23")
    await expect(value).toContainText(/^\d+\.\d{2}$/);
  });

  test('muestra la sección de Métricas de riesgo', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Métricas de riesgo' }),
    ).toBeVisible();
  });

  test('muestra el cortacircuitos en la sección de riesgo', async ({ page }) => {
    const riskSection = page.locator('div', {
      has: page.getByRole('heading', { level: 2, name: 'Métricas de riesgo' }),
    });
    await expect(riskSection.getByText(/Cortacircuitos:/i)).toBeVisible();
    // Should show either ACTIVO or INACTIVO
    await expect(riskSection.getByText(/ACTIVO|INACTIVO/i)).toBeVisible();
  });

  test('muestra las pérdidas consecutivas en la sección de riesgo', async ({ page }) => {
    const riskSection = page.locator('div', {
      has: page.getByRole('heading', { level: 2, name: 'Métricas de riesgo' }),
    });
    await expect(riskSection.getByText(/Pérdidas consecutivas:/i)).toBeVisible();
  });

  test('muestra el factor de beneficio en la sección de riesgo', async ({ page }) => {
    const riskSection = page.locator('div', {
      has: page.getByRole('heading', { level: 2, name: 'Métricas de riesgo' }),
    });
    await expect(riskSection.getByText(/Factor de beneficio:/i)).toBeVisible();
  });

  test('muestra el estado de operación del bot', async ({ page }) => {
    // Either "En operación" (green) or "Bot detenido" (red)
    const status = page.getByText(/En operación|Bot detenido/i);
    await expect(status).toBeVisible();
  });
});

test.describe('Dashboard — Curva de capital (Recharts)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('la sección Curva de capital es visible', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Curva de capital' }),
    ).toBeVisible();
  });

  test('el gráfico de curva de capital se renderiza con SVG', async ({ page }) => {
    // Wait for the heading to confirm data has loaded (not loading state).
    await expect(
      page.getByRole('heading', { level: 2, name: 'Curva de capital' }),
    ).toBeVisible();
    // Recharts renders an <svg> inside the chart container.
    const chartSection = page.locator('div', {
      has: page.getByRole('heading', { level: 2, name: 'Curva de capital' }),
    });
    await expect(chartSection.locator('svg')).toBeVisible();
  });

  test('la curva muestra la leyenda con el Balance', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Curva de capital' }),
    ).toBeVisible();
    // Recharts Legend renders the series name "Balance".
    await expect(page.getByText('Balance', { exact: true }).first()).toBeVisible();
  });
});

test.describe('Dashboard — Registro de operaciones', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('la sección Registro de operaciones es visible', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
  });

  test('la tabla de operaciones tiene encabezados de columna correctos', async ({ page }) => {
    // Wait for heading to confirm we're past loading.
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    const expectedHeaders = [
      'Hora',
      'Dirección',
      'Entrada',
      'Salida',
      'Stop de pérdida',
      'Objetivo de ganancia',
      'Stake',
      'Confianza',
      'Resultado',
      'Motivo de salida',
      'Estado',
    ];
    for (const header of expectedHeaders) {
      await expect(
        table.getByRole('columnheader', { name: header }),
      ).toBeVisible();
    }
  });

  test('la tabla de operaciones carga al menos una fila de datos', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    // Wait for tbody to have row(s) of data.
    await expect(table.locator('tbody tr').first()).toBeVisible();
    const rowCount = await table.locator('tbody tr').count();
    expect(rowCount).toBeGreaterThan(0);
  });

  test('cada fila de operaciones muestra una dirección LONG o SHORT', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    await expect(table.locator('tbody tr').first()).toBeVisible();
    // Direction column — cells contain "LONG" or "SHORT"
    const directions = await table.locator('tbody tr td:nth-child(2)').allTextContents();
    expect(directions.length).toBeGreaterThan(0);
    for (const dir of directions) {
      expect(['LONG', 'SHORT']).toContain(dir.trim());
    }
  });

  test('cada fila muestra un resultado monetario con $', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    await expect(table.locator('tbody tr').first()).toBeVisible();
    const results = await table.locator('tbody tr td:nth-child(9)').allTextContents();
    expect(results.length).toBeGreaterThan(0);
    for (const res of results) {
      expect(res).toContain('$');
    }
  });

  test('el estado de cada operación es WON, LOST o vacío', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Registro de operaciones' }),
    ).toBeVisible();
    const table = page.getByRole('table');
    await expect(table.locator('tbody tr').first()).toBeVisible();
    const statuses = await table.locator('tbody tr td:nth-child(11)').allTextContents();
    expect(statuses.length).toBeGreaterThan(0);
    // Each cell shows WON, LOST, or '—'
    for (const s of statuses) {
      const trimmed = s.trim();
      expect(['WON', 'LOST', '—', '']).toContain(trimmed);
    }
  });
});
