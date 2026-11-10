import { expect, type Locator, type Page } from './fixtures/test';

/** 原生スクロールバーとタッチを実際に操作する。DOM の focus/scroll handler は差し替えない。 */
export async function exerciseSelectScrollbar(page: Page, list: Locator, touch: boolean) {
  await list.scrollIntoViewIfNeeded();
  const box = await list.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, gutter: (element as HTMLElement).offsetWidth - element.clientWidth, overflow: element.scrollHeight - element.clientHeight };
  });
  expect(box.overflow).toBeGreaterThan(0);
  if (!touch) {
    expect(box.gutter).toBeGreaterThan(0);
    const x = box.x + box.width - box.gutter / 2;
    await page.mouse.move(x, box.y + box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(x, box.y + box.height * 0.8, { steps: 8 });
    await page.mouse.up();
    await expect(list).toBeVisible();
    await list.evaluate(element => { element.scrollTop = 0; });
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 160);
  } else {
    const session = await page.context().newCDPSession(page);
    const x = box.x + box.width / 2;
    const y = box.y + box.height * 0.8;
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let i = 1; i <= 8; i++) await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - box.height * 0.6 * i / 8 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await session.detach();
  }
  await expect(list).toBeVisible();
  await expect.poll(() => list.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
}
