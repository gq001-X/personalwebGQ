/* Optional browser QA. Production app does not require Node or Playwright. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.REPOLENS_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto('http://127.0.0.1:8501', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '一键演示 · 扫描示例项目', exact: true }).waitFor({timeout: 60000});
    const out = path.resolve(__dirname, '../.runtime');
    fs.mkdirSync(out, {recursive: true});
    await page.screenshot({path: path.join(out, 'welcome.png'), fullPage: true});
    await page.getByRole('button', { name: '一键演示 · 扫描示例项目', exact: true }).click();
    await page.getByRole('tab', {name: '调用关系', exact: true}).waitFor({timeout:60000});
    assert.equal(await page.locator('[data-testid="stException"]').count(), 0);
    await page.screenshot({path: path.join(out, 'overview.png'), fullPage: true});
    await page.getByRole('tab', {name: '调用关系', exact: true}).click();
    await page.waitForTimeout(1200);
    await page.screenshot({path: path.join(out, 'call-graph.png'), fullPage: true});
    await page.getByRole('tab', {name: '问题与证据', exact: true}).click();
    await page.getByText('源码证据', {exact:true}).first().waitFor();
    await page.screenshot({path: path.join(out, 'evidence.png'), fullPage:true});
    await page.getByRole('tab', {name: '代码问答', exact:true}).click();
    await page.getByRole('button', {name:'有哪些高风险问题？',exact:true}).click();
    await page.getByText('按潜在影响排序，以下项目需要人工复核：', {exact:false}).waitFor();
    await page.screenshot({path:path.join(out,'chat.png'),fullPage:true});
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button',{name:'下载 Markdown 报告',exact:true}).click();
    const download = await downloadPromise;
    await download.saveAs(path.join(out,'browser-report.md'));
    const report = fs.readFileSync(path.join(out,'browser-report.md'),'utf8');
    assert(report.includes('PY-DYNAMIC-EXEC'));
    assert(report.includes('SHA-256'));
    await page.reload({waitUntil:'domcontentloaded'});
    await page.getByRole('button',{name:'加载此历史快照',exact:true}).waitFor();
    await page.getByRole('button',{name:'加载此历史快照',exact:true}).click();
    await page.getByText('当前展示历史快照', {exact:false}).waitFor();
    await page.getByRole('tab',{name:'代码问答',exact:true}).click();
    await page.getByText('按潜在影响排序，以下项目需要人工复核：', {exact:false}).waitFor();
    assert.equal(await page.locator('[data-testid="stException"]').count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['load','scan','graph','evidence','chat','download','history-reload'],screenshots:out}));
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(err=>{console.error(err);process.exit(1);});
