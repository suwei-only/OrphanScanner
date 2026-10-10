# OrphanScanner v1.5 改动计划(2026-10-08)

目标:树图交互对齐 disktree + 全盘扫描 + 应用归属识别。版本 APP_VERSION 1.2.0 → 1.5.0。

## 新增文件
1. `app_identity.py` — 归属识别内核(纯函数可测)
   数据源按优先级:①卸载注册表 InstallLocation+DisplayName(复用 uninstall_entries)
   ②Steam/Epic manifest ③内置知识库(微信/QQ/企业微信/钉钉/百度网盘/夸克/网易云/
   Chrome/Edge/JetBrains/VS Code/npm/pip/conda 等路径组件)
   ④开始菜单 .lnk(复用 start_menu_links) ⑤UWP Get-AppxPackage ⑥运行进程目录
   匹配:normcase 目录前缀最深匹配;同深度按来源优先级。结果 (display, source_label),
   未知 (None, None)。sources 带 TTL 缓存,GUI 侧后台线程调用防卡界面。
2. `full_scan.py` — 全盘扫描器
   `list_volumes()`:GetLogicalDrives+GetDiskFreeSpaceEx → [(letter,label,free,total)] 最满在前
   NTFS 分配大小:GetFileInformationByHandleEx FileIdExtdDirectoryInfo(ctypes,目录级枚举
   AllocationSize);拿不到退化 st_size 并返回口径标记
   硬链接去重:st_nlink>1 才查 FileIdInfo (VolumeSerial+FileId) 去重
   无权限目录跳过,记录 skip 列表 [(path,errno)];渐进回调 on_dir/on_file + stop 可取消
3. `PLAN_v1.5.md`(本文件)

## 修改文件
4. `disk_visualizer.py` — 树图交互升级
   悬停高亮+tooltip(路径/大小/占比,防抖 200ms)
   单击目录下钻;面包屑路径栏点击跳级;同级大目录列表(top-N 按大小)横跳
   滚轮缩放(MIN_PX 8~64);右侧选中面板:大小大字/占扫描总量%/文件数/最后修改时间/
   应用归属(异步识别)/空格标记待删按钮
   标记清单:空格 toggle;标记目录吸收其内部已标记项(不可反向恢复);清单显示
   每项路径+大小+合计释放空间;复核后逐项走 move_to_trash(受系统目录拒删保护)
5. `orphan_scanner.py` — `is_protected_path()`(C:\Windows、Program Files、
   ProgramData、System Volume Information、$Recycle.Bin、pagefile/hiberfil/swapfile),
   move_to_trash 接入保护;APP_VERSION="1.5.0"
6. `orphan_gui.py` — 磁盘地图盘符选择器动态化(full_scan.list_volumes,显示剩余空间,
   最满在前);默认扫描用户目录(~),可切盘全盘;主窗口标题/帮助文案更新
7. `tests/test_core.py` — 新增 TestAppIdentity/TestFullScan/TestProtectedPath/
   TestMarkAbsorb(全部零副作用:tempfile+HKCU 临时键+注入假 sources)

## 硬约束
- 测试:PYTHONPATH= E:/Anaconda/python.exe -m unittest discover -s tests -v(15 基线全绿)
- 轻依赖:tkinter+stdlib+ctypes;PS 子进程 UTF-8 前缀+errors=replace;路径引号
- 删除铁律:只走回收站、注册表先 .reg 备份、系统目录拒删
- 打包:PyInstaller onefile → 桌面副本覆盖(先查进程)→ md5 验证 → 窗口标题读版本

## 顺序
归属内核 → 全盘扫描器 → 树图交互 → GUI 集成 → 保护路径+版本 bump → 测试 → 打包

## 状态(2026-10-10 全部完成 ✅)
- 全部 7 步完成;测试套件 15→34 全绿
- 打包 dist/OrphanScanner.exe(11.86MB,md5 3f444b80ba841b7f4f82a4033680816c),
  桌面副本已同步(md5 一致);窗口标题实测 'OrphanScanner v1.5.0 — 孤儿文件扫描器'
- 关键修正:FILE_ID_EXTD_DIR_INFO 变长结构解析不可靠(名字区偏移错乱)
  → 改用文件级 FileStandardInfo(固定24字节,一次拿 AllocationSize+NumberOfLinks)
- commit 9205781;GitHub push 受 CN 网络影响走重试循环
