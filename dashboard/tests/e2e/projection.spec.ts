import { test, expect } from '@playwright/test';

/**
 * /projection — Zona de Proyección Económica
 * Coverage: capital allocator sliders, attribution table, Monte Carlo chart,
 * time-horizon tabs (7D/30D/90D), reindirection switch.
 */

test.describe('Projection — carga de elementos principales', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('renderiza la región Asignador de capital', async ({ page }) => {
    await expect(
      page.getByRole('region', { name: /Asignador de capital/i }),
    ).toBeVisible();
  });

  test('renderiza la región Proyección y atribución', async ({ page }) => {
    await expect(
      page.getByRole('region', { name: /Proyección y atribución/i }),
    ).toBeVisible();
  });

  test('muestra el título de sección Asignador de capital', async ({ page }) => {
    await expect(page.getByText(/Asignador de capital/i).first()).toBeVisible();
  });

  test('muestra el texto de superávit operativo', async ({ page }) => {
    await expect(page.getByText(/Superávit operativo/i).first()).toBeVisible();
  });

  test('muestra el Wallet total en el header', async ({ page }) => {
    await expect(page.getByText(/Wallet/i).first()).toBeVisible();
    await expect(page.getByText(/Wallet total/i)).toBeVisible();
  });
});

test.describe('Projection — sliders del asignador', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('el slider Reserva base está presente y tiene un valor inicial', async ({ page }) => {
    const slider = page.getByRole('slider', { name: /Reserva base/i });
    await expect(slider).toBeVisible();
    // Default value from DATA.reservePct = 80
    await expect(slider).toHaveValue('80');
  });

  test('el slider Superávit está presente y tiene un valor inicial', async ({ page }) => {
    const slider = page.getByRole('slider', { name: /Superávit/i });
    await expect(slider).toBeVisible();
    // Default value from DATA.surplusPct = 20
    await expect(slider).toHaveValue('20');
  });

  test('al mover el slider de Reserva, el valor porcentual se actualiza', async ({ page }) => {
    const slider = page.getByRole('slider', { name: /Reserva base/i });
    await expect(slider).toBeVisible();
    // The label shows "{reservePct}%" — read the current text first.
    const label = page.getByText(/Reserva base \(/i).first();
    await expect(label).toContainText(/80%/);

    // Move slider to 85 (keyboard — Playwright slider fill via setValue)
    await slider.fill('85');
    await expect(slider).toHaveValue('85');
    // The breakdown label should now reflect 85%
    await expect(page.getByText(/Reserva base \(85%\)/i)).toBeVisible();
  });

  test('al mover el slider de Superávit, el valor porcentual se actualiza', async ({ page }) => {
    const slider = page.getByRole('slider', { name: /Superávit/i });
    await expect(slider).toBeVisible();
    await expect(page.getByText(/Superávit \(20%\)/i)).toBeVisible();

    await slider.fill('30');
    await expect(slider).toHaveValue('30');
    await expect(page.getByText(/Superávit \(30%\)/i)).toBeVisible();
  });
});

test.describe('Projection — switch de reinversión', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('el switch de reinversión está activado por defecto', async ({ page }) => {
    const toggle = page.getByRole('switch', { name: /Reinversión de ganancias/i });
    await expect(toggle).toBeVisible();
    await expect(toggle).toBeChecked();
  });

  test('al hacer clic en el switch, se desactiva', async ({ page }) => {
    const toggle = page.getByRole('switch', { name: /Reinversión de ganancias/i });
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();
  });

  test('al hacer clic de nuevo, el switch se reactiva', async ({ page }) => {
    const toggle = page.getByRole('switch', { name: /Reinversión de ganancias/i });
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(toggle).toBeChecked();
  });
});

test.describe('Projection — tabla de atribución por estrategia', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('la sección Análisis por estrategia es visible', async ({ page }) => {
    await expect(page.getByText(/Análisis por estrategia/i)).toBeVisible();
  });

  test('la tabla de atribución tiene los encabezados correctos', async ({ page }) => {
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    const headers = [
      'Estrategia',
      'Mejor símbolo',
      'Resultado',
      'Tasa de aciertos',
      'Índice de rendimiento',
      'Rating',
    ];
    for (const header of headers) {
      await expect(
        table.getByRole('columnheader', { name: new RegExp(`^${header}$`, 'i') }),
      ).toBeVisible();
    }
  });

  test('la tabla muestra las 4 estrategias esperadas', async ({ page }) => {
    const table = page.getByRole('table');
    await expect(table.getByRole('cell', { name: 'RangeBreak' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'Volatility' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'MeanReversion' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'PairTrading' })).toBeVisible();
  });

  test('la tabla muestra los ratings correctos (BEST, OK, DROP)', async ({ page }) => {
    const table = page.getByRole('table');
    await expect(table.getByRole('cell', { name: 'BEST' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'OK' }).first()).toBeVisible();
    await expect(table.getByRole('cell', { name: 'DROP' })).toBeVisible();
  });

  test('RangeBreak tiene el mejor PnL y rating BEST', async ({ page }) => {
    const table = page.getByRole('table');
    const rangeRow = table.locator('tr', { has: table.getByRole('cell', { name: 'RangeBreak' }) });
    await expect(rangeRow.getByRole('cell', { name: 'BEST' })).toBeVisible();
    await expect(rangeRow.getByText(/\$14\.20/)).toBeVisible();
  });

  test('PairTrading muestra rating DROP y guiones en métricas', async ({ page }) => {
    const table = page.getByRole('table');
    const pairRow = table.locator('tr', { has: table.getByRole('cell', { name: 'PairTrading' }) });
    await expect(pairRow.getByRole('cell', { name: 'DROP' })).toBeVisible();
    // Win rate and Sharpe show "—" for PairTrading
    const cells = pairRow.locator('td');
    await expect(cells.nth(3)).toContainText('—'); // Tasa de aciertos
    await expect(cells.nth(4)).toContainText('—'); // Índice de rendimiento
  });
});

test.describe('Projection — gráfico Monte Carlo', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('el título de la simulación es visible', async ({ page }) => {
    await expect(page.getByText(/Curva de capital — Simulación de escenarios/i)).toBeVisible();
  });

  test('el subtítulo describe las 10,000 simulaciones y bandas P5/P50/P95', async ({ page }) => {
    await expect(page.getByText(/10,000 simulaciones/i)).toBeVisible();
    await expect(page.getByText(/P5\/P50\/P95/i)).toBeVisible();
  });

  test('el SVG del gráfico Monte Carlo se renderiza', async ({ page }) => {
    // The Monte Carlo chart is an inline SVG with an aria-label.
    const chart = page.getByRole('img', { name: /Curva de equity con bandas percentiles/i });
    await expect(chart).toBeVisible();
    // Confirm SVG child elements are present (paths, circles).
    const svg = chart.locator('xpath=ancestor-or-self::svg[1]');
    await expect(svg.locator('path').first()).toBeVisible();
    await expect(svg.locator('circle').first()).toBeVisible();
  });

  test('la leyenda muestra las bandas P5, P50 y P95', async ({ page }) => {
    await expect(page.getByText(/P50 \(mediana\)/i)).toBeVisible();
    await expect(page.getByText(/P95 \(óptimo\)/i)).toBeVisible();
    await expect(page.getByText(/P5 \(pesimista\)/i)).toBeVisible();
  });

  test('las stats de proyección muestran valores monetarios', async ({ page }) => {
    await expect(page.getByText(/\$48\.20/)).toBeVisible(); // P50
    await expect(page.getByText(/\$72\.40/)).toBeVisible(); // P95
    await expect(page.getByText(/−\$12\.80|-\$12\.80/)).toBeVisible(); // P5
  });

  test('el eje X muestra la etiqueta D0 (día inicial)', async ({ page }) => {
    await expect(page.getByText('D0', { exact: true })).toBeVisible();
  });
});

test.describe('Projection — tabs de horizonte temporal (7D/30D/90D)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/projection');
  });

  test('el tablist Horizonte temporal es visible con 3 tabs', async ({ page }) => {
    const tablist = page.getByRole('tablist', { name: /Horizonte temporal/i });
    await expect(tablist).toBeVisible();
    await expect(tablist.getByRole('tab', { name: '7D' })).toBeVisible();
    await expect(tablist.getByRole('tab', { name: '30D' })).toBeVisible();
    await expect(tablist.getByRole('tab', { name: '90D' })).toBeVisible();
  });

  test('el tab 7D está seleccionado por defecto', async ({ page }) => {
    const tab = page.getByRole('tab', { name: '7D' });
    await expect(tab).toHaveAttribute('aria-selected', 'true');
  });

  test('al hacer clic en 30D, ese tab se selecciona y 7D se desactiva', async ({ page }) => {
    const tab7 = page.getByRole('tab', { name: '7D' });
    const tab30 = page.getByRole('tab', { name: '30D' });
    expect(await tab7.getAttribute('aria-selected')).toBe('true');

    await tab30.click();
    await expect(tab30).toHaveAttribute('aria-selected', 'true');
    await expect(tab7).toHaveAttribute('aria-selected', 'false');
  });

  test('al hacer clic en 90D, ese tab se selecciona', async ({ page }) => {
    const tab90 = page.getByRole('tab', { name: '90D' });
    await tab90.click();
    await expect(tab90).toHaveAttribute('aria-selected', 'true');

    const tab7 = page.getByRole('tab', { name: '7D' });
    await expect(tab7).toHaveAttribute('aria-selected', 'false');
  });

  test('al cambiar a 30D el gráfico muestra etiquetas de días D8/D16/D24', async ({ page }) => {
    await page.getByRole('tab', { name: '30D' }).click();
    // The SVG renders day labels depending on horizon.
    await expect(page.getByText('D8', { exact: true })).toBeVisible();
    await expect(page.getByText('D30', { exact: true })).toBeVisible();
  });

  test('al cambiar a 90D el gráfico muestra etiquetas D24/D48/D72/D90', async ({ page }) => {
    await page.getByRole('tab', { name: '90D' }).click();
    await expect(page.getByText('D24', { exact: true })).toBeVisible();
    await expect(page.getByText('D90', { exact: true })).toBeVisible();
  });

  test('el subtítulo de atribución cambia con el horizonte', async ({ page }) => {
    await page.getByRole('tab', { name: '30D' }).click();
    await expect(page.getByText(/· 30D/i)).toBeVisible();
    await page.getByRole('tab', { name: '90D' }).click();
    await expect(page.getByText(/· 90D/i)).toBeVisible();
  });

  test('la descripción del P50 menciona el horizonte activo en días', async ({ page }) => {
    await page.getByRole('tab', { name: '30D' }).click();
    await expect(page.getByText(/30 días/i)).toBeVisible();
    await page.getByRole('tab', { name: '90D' }).click();
    await expect(page.getByText(/90 días/i)).toBeVisible();
    await page.getByRole('tab', { name: '7D' }).click();
    await expect(page.getByText(/7 días/i)).toBeVisible();
  });
});
