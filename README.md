# 韭菜情绪追踪日报自动发送

这个目录里已经放好一个本地自动化程序，用来在每个工作日早晨生成并发送类似截图样式的 HTML 邮件。

## 文件

- `sentiment_dashboard.py`：生成看板、采集数据、发送邮件。
- `.env.example`：配置样例。
- `install_windows_task.ps1`：注册 Windows 计划任务，默认每天 08:00 触发；脚本会跳过非工作日。

## 首次配置

1. 复制 `.env.example` 为 `.env`。
2. 在 `.env` 里填写 `SMTP_USER` 和 `SMTP_PASSWORD`。
3. QQ 邮箱的 `SMTP_PASSWORD` 必须填写“授权码”，不是 QQ 登录密码。
4. 可选填写 `XUEQIU_COOKIE`，用于增强雪球搜索数据采集。

## 测试预览

```powershell
python .\sentiment_dashboard.py --dry-run
```

运行后会生成 `dashboard_preview.html`。

## 立即发送一次

```powershell
python .\sentiment_dashboard.py
```

## 注册每天工作日 8 点发送

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1
```

如需指定 Python 路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -PythonExe "C:\Path\To\python.exe"
```

## 数据源说明

程序会优先尝试采集：

- 雪球搜索数据：需要 `XUEQIU_COOKIE`。
- 东方财富股吧公开页面：无需登录，按页面可访问情况抓取。

如果外部页面临时不可访问，程序会使用本地词典降级生成日报，保证定时任务不会因为数据源波动直接中断。

## GitHub Actions 云端运行

仓库已经包含 `.github/workflows/daily-dashboard.yml`，上传到 GitHub 后会在北京时间每天 08:00 触发。

需要在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 配置 Secrets：

- `SMTP_HOST`：`smtp.qq.com`
- `SMTP_PORT`：`465`
- `SMTP_USER`：发件邮箱
- `SMTP_PASSWORD`：QQ 邮箱 SMTP 授权码
- `MAIL_TO`：收件人，英文逗号分隔
- `XUEQIU_COOKIE`：可选

节假日和调休可配置为 Variables：

- `HOLIDAY_DATES`
- `WORKDAY_DATES`

## 工作日判断

默认周一到周五发送、周六周日跳过。若遇到法定节假日或调休补班，在 `.env` 中维护：

```env
HOLIDAY_DATES=2026-05-01,2026-05-04
WORKDAY_DATES=2026-05-09
```
