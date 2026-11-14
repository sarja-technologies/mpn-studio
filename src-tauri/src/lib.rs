//! MPN Studio desktop shell.
//!
//! The window shows the bundled web UI (tauri://localhost). A local backend (the MPN Studio API, a PyInstaller
//! bundle shipped inside the app) is started on a random loopback port with a per-launch token, and stopped when
//! the app exits. Nothing here talks to any other host. File dialogs and "open in file manager" are app commands.

use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    os::unix::process::CommandExt,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_window_state::{StateFlags, WindowExt};

struct Backend {
    child: Mutex<Option<Child>>,
    port: u16,
    token: String,
    log: PathBuf,
    start_error: Mutex<Option<String>>,
}
type Shared = Arc<Backend>;

/// Backup files the system asked the app to open (command line / "Open with"), until the window has taken them.
#[derive(Default)]
struct OpenFiles(Mutex<Vec<String>>);

/// `.mpnbak` files among the arguments. Relative paths are resolved against the directory the second launch ran in.
fn backup_args(args: &[String], cwd: &str) -> Vec<String> {
    args.iter()
        .skip(1)
        .filter(|a| a.ends_with(".mpnbak") || a.ends_with(".mpn"))
        .map(|a| {
            let p = PathBuf::from(a);
            if p.is_absolute() { p } else { PathBuf::from(cwd).join(p) }
        })
        .filter(|p| p.is_file())
        .map(|p| p.to_string_lossy().to_string())
        .collect()
}

#[tauri::command]
fn take_open_files(state: State<'_, OpenFiles>) -> Vec<String> {
    std::mem::take(&mut *state.0.lock().unwrap())
}

#[derive(Serialize)]
struct Info {
    base: String,
    token: String,
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}

/// XDG state directory (logs). MPN_STUDIO_STATE_DIR overrides, as in the backend.
fn state_dir() -> PathBuf {
    if let Ok(v) = std::env::var("MPN_STUDIO_STATE_DIR") {
        return PathBuf::from(v);
    }
    match std::env::var("XDG_STATE_HOME") {
        Ok(v) if v.starts_with('/') => PathBuf::from(v),
        _ => home().join(".local/state"),
    }
    .join("mpn-studio")
}

fn random_token() -> String {
    let mut b = [0u8; 24];
    File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut b)).expect("no /dev/urandom");
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn free_port() -> std::io::Result<u16> {
    let l = TcpListener::bind("127.0.0.1:0")?;
    Ok(l.local_addr()?.port())
}

fn tail(path: &PathBuf, n: usize) -> String {
    let s = fs::read(path).map(|b| String::from_utf8_lossy(&b).to_string()).unwrap_or_default();
    let lines: Vec<&str> = s.lines().collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}

fn backend_command(app: &AppHandle) -> Result<Command, String> {
    // Development: run the source tree with a Python of your choice.
    if let Ok(py) = std::env::var("MPN_DEV_PYTHON") {
        let mut c = Command::new(py);
        c.args(["-m", "mpnstudio.api.app"]);
        if let Ok(root) = std::env::var("MPN_DEV_ROOT") {
            c.current_dir(root);
        }
        return Ok(c);
    }
    let res = app.path().resource_dir().map_err(|e| e.to_string())?;
    let exe = res.join("backend").join("mpn-backend");
    if !exe.is_file() {
        return Err(format!("The local backend is missing from this installation ({}).", exe.display()));
    }
    let mut c = Command::new(exe);
    // llama.cpp builds for this machine architecture, shipped next to the backend
    if std::env::var_os("MPN_RUNTIME_DIR").is_none() {
        c.env("MPN_RUNTIME_DIR", res.join("runtime"));
    }
    Ok(c)
}

fn start_backend(app: &AppHandle) -> Backend {
    let log = state_dir().join("logs").join("backend.log");
    let _ = fs::create_dir_all(log.parent().unwrap());
    // keep the log bounded: start fresh when it passes 2 MiB
    if fs::metadata(&log).map(|m| m.len() > 2 << 20).unwrap_or(false) {
        let _ = fs::rename(&log, log.with_extension("log.1"));
    }
    let port = free_port().unwrap_or(0);
    let token = random_token();
    let mut be = Backend { child: Mutex::new(None), port, token: token.clone(), log: log.clone(), start_error: Mutex::new(None) };
    let spawn = || -> Result<Child, String> {
        let mut cmd = backend_command(app)?;
        let out = OpenOptions::new().create(true).append(true).open(&log).map_err(|e| e.to_string())?;
        let err = out.try_clone().map_err(|e| e.to_string())?;
        cmd.env("MPN_DESKTOP", "1")
            .env("MPN_HOST", "127.0.0.1")
            .env("MPN_PORT", port.to_string())
            .env("MPN_SESSION_TOKEN", &token)
            .env("MPN_NO_SYSTEMD", "1")
            .env("MPN_ACCESS_LOG", std::env::var("MPN_ACCESS_LOG").unwrap_or_default())
            .env("MPN_ALLOW_ORIGINS", "tauri://localhost,http://tauri.localhost")
            .env_remove("MPN_DB")
            .env_remove("MPN_DATA")
            .env_remove("MPN_PROJECT")
            .stdin(Stdio::null())
            .stdout(Stdio::from(out))
            .stderr(Stdio::from(err));
        // If this app is killed without a chance to clean up, the backend still receives SIGTERM.
        unsafe {
            cmd.pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                Ok(())
            });
        }
        cmd.spawn().map_err(|e| format!("Could not start the local backend: {e}"))
    };
    match spawn() {
        Ok(c) => *be.child.get_mut().unwrap() = Some(c),
        Err(e) => *be.start_error.get_mut().unwrap() = Some(e),
    }
    be
}

impl Backend {
    fn healthy(&self) -> bool {
        let addr: SocketAddr = ([127, 0, 0, 1], self.port).into();
        let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else { return false };
        let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
        let req = format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nx-mpn-token: {}\r\nConnection: close\r\n\r\n", self.token);
        if s.write_all(req.as_bytes()).is_err() {
            return false;
        }
        let mut buf = String::new();
        let _ = s.read_to_string(&mut buf);
        buf.starts_with("HTTP/1.1 200")
    }

    fn wait_ready(&self) -> Result<(), String> {
        if let Some(e) = self.start_error.lock().unwrap().clone() {
            return Err(e);
        }
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(60) {
            if let Some(c) = self.child.lock().unwrap().as_mut() {
                if let Ok(Some(status)) = c.try_wait() {
                    return Err(format!("The local backend stopped at startup ({status}).\n{}\n\nLog: {}", tail(&self.log, 12), self.log.display()));
                }
            }
            if self.healthy() {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(150));
        }
        Err(format!("The local backend did not answer within 60 seconds.\n{}\n\nLog: {}", tail(&self.log, 12), self.log.display()))
    }

    /// SIGTERM so the backend can checkpoint SQLite and close the project, then SIGKILL if it hangs.
    fn stop(&self) {
        let Some(mut c) = self.child.lock().unwrap().take() else { return };
        unsafe { libc::kill(c.id() as i32, libc::SIGTERM) };
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(8) {
            if let Ok(Some(_)) = c.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = c.kill();
        let _ = c.wait();
    }
}

#[tauri::command]
async fn backend_info(state: State<'_, Shared>) -> Result<Info, String> {
    let be = state.inner().clone();
    let b2 = be.clone();
    tauri::async_runtime::spawn_blocking(move || b2.wait_ready()).await.map_err(|e| e.to_string())??;
    Ok(Info { base: format!("http://127.0.0.1:{}", be.port), token: be.token.clone() })
}

#[tauri::command]
async fn pick_folder(app: AppHandle, title: String) -> Result<Option<String>, String> {
    let p = app.dialog().file().set_title(title).blocking_pick_folder();
    Ok(p.and_then(|f| f.into_path().ok()).map(|p| p.to_string_lossy().to_string()))
}

#[tauri::command]
async fn pick_file(app: AppHandle, title: String, extensions: Vec<String>) -> Result<Option<String>, String> {
    let mut d = app.dialog().file().set_title(title);
    if !extensions.is_empty() {
        let ext: Vec<&str> = extensions.iter().map(|s| s.as_str()).collect();
        d = d.add_filter("Files", &ext);
    }
    let p = d.blocking_pick_file();
    Ok(p.and_then(|f| f.into_path().ok()).map(|p| p.to_string_lossy().to_string()))
}

fn xdg_open(target: &str) -> Result<(), String> {
    Command::new("xdg-open")
        .arg(target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Could not open it: {e}"))
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.is_absolute() || !p.exists() {
        return Err("That location does not exist.".into());
    }
    xdg_open(&path)
}

/// Only for links the user clicks (provenance sources). The window itself never navigates to them.
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("Only web links can be opened.".into());
    }
    xdg_open(&url)
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn set_window_title(window: WebviewWindow, title: String) -> Result<(), String> {
    window.set_title(&title).map_err(|e| e.to_string())
}

/// `mpn-studio mpn …` runs the bundled `mpn` command line (run, inspect, verify .mpn files) without opening a window.
/// The backend and runtime sit beside the binary: <prefix>/bin/mpn-studio and <prefix>/lib/MPN Studio/ in both the
/// .deb (/usr) and the AppImage ($APPDIR/usr).
fn cli_passthrough() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) != Some("mpn") {
        return;
    }
    let exe = std::env::current_exe().ok().and_then(|p| fs::canonicalize(p).ok());
    let lib = exe.as_ref().and_then(|p| p.parent()).and_then(|p| p.parent()).map(|p| p.join("lib").join("MPN Studio"));
    let backend = lib.as_ref().map(|l| l.join("backend").join("mpn-backend"));
    match (lib, backend) {
        (Some(lib), Some(b)) if b.is_file() => {
            let mut c = Command::new(b);
            c.args(&args[1..]);
            if std::env::var_os("MPN_RUNTIME_DIR").is_none() {
                c.env("MPN_RUNTIME_DIR", lib.join("runtime"));
            }
            let e = c.exec();
            eprintln!("mpn: could not start the bundled backend: {e}");
            std::process::exit(1);
        }
        _ => {
            eprintln!("mpn: the bundled backend is missing from this installation");
            std::process::exit(1);
        }
    }
}

/// A size saved on a larger monitor must not leave the window bigger than the one it opens on now: shrink it to fit
/// and centre it. The saved size is read from the window-state file because restoring it is applied asynchronously,
/// so the window does not report it yet; the resize below is queued after the restore and wins.
fn fit_on_screen(app: &tauri::AppHandle) {
    let Some(w) = app.get_webview_window("main") else { return };
    let saved = app
        .path()
        .app_config_dir()
        .ok()
        .and_then(|d| std::fs::read_to_string(d.join(".window-state.json")).ok())
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| {
            let m = v.get("main")?;
            if m.get("maximized").and_then(|x| x.as_bool()).unwrap_or(false) || m.get("fullscreen").and_then(|x| x.as_bool()).unwrap_or(false) {
                return None;
            }
            Some((m.get("width")?.as_u64()? as u32, m.get("height")?.as_u64()? as u32))
        });
    let Some((sw, sh)) = saved else { return };
    let mon = match w.current_monitor() {
        Ok(Some(m)) => Some(m),
        _ => w.primary_monitor().ok().flatten(),
    };
    let Some(m) = mon else { return };
    let (mw, mh) = (m.size().width, m.size().height);
    if sw <= mw && sh <= mh {
        return;
    }
    // leave room for the title bar and panels the window manager adds
    let width = sw.min(mw * 95 / 100).max(1);
    let height = sh.min(mh * 90 / 100).max(1);
    let _ = w.set_size(tauri::PhysicalSize::new(width, height));
    // centre on that monitor; center() would use the size the window has not taken yet
    let p = m.position();
    let _ = w.set_position(tauri::PhysicalPosition::new(p.x + ((mw - width) / 2) as i32, p.y + ((mh - height) / 2) as i32));
}

pub fn run() {
    cli_passthrough();
    // WebKitGTK's DMABUF renderer is a known source of blank windows and web-process crashes on NVIDIA/Wayland.
    // The board is a canvas, so the fallback path costs nothing visible. Set the variable yourself to override.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let files = backup_args(&args, &cwd);
                if !files.is_empty() {
                    app.state::<OpenFiles>().0.lock().unwrap().extend(files);
                    let _ = w.eval("window.dispatchEvent(new Event('mpn-open-files'))");
                }
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        // the main window's saved size and position are restored in setup, before it is fitted to the current monitor
        .plugin(tauri_plugin_window_state::Builder::default().skip_initial_state("main").build())
        .setup(|app| {
            let backend: Shared = Arc::new(start_backend(app.handle()));
            app.manage(backend);
            let cwd = std::env::current_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
            let first = std::env::args().collect::<Vec<_>>();
            app.manage(OpenFiles(Mutex::new(backup_args(&first, &cwd))));
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("MPN Studio")
                .inner_size(1440.0, 900.0)
                .min_inner_size(900.0, 600.0)
                // the window may only ever show the bundled UI
                .on_navigation(|url| {
                    matches!(url.scheme(), "tauri" | "about" | "blob" | "data") || url.host_str() == Some("tauri.localhost")
                })
                .build()?;
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.restore_state(StateFlags::all());
            }
            fit_on_screen(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![take_open_files, backend_info, pick_folder, pick_file, reveal_path, open_external, quit_app, set_window_title])
        .build(tauri::generate_context!())
        .expect("error while building MPN Studio");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(be) = handle.try_state::<Shared>() {
                be.stop();
            }
        }
    });
}
