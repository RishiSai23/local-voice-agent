// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    Emitter, Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[tauri::command]
fn show_companion(app_handle: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app_handle.get_webview_window("companion") {
        window.show().map_err(|e| e.to_string())?;
        window.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn hide_companion(app_handle: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app_handle.get_webview_window("companion") {
        window.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn toggle_companion(app_handle: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app_handle.get_webview_window("companion") {
        let is_visible = window.is_visible().unwrap_or(false);
        let is_focused = window.is_focused().unwrap_or(false);

        if is_visible && is_focused {
            window.hide().map_err(|e| e.to_string())?;
        } else {
            window.show().map_err(|e| e.to_string())?;
            window.set_focus().map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        let alt_j: Shortcut = "Alt+J".parse().unwrap();
                        if shortcut == &alt_j {
                            if let Some(window) = app.get_webview_window("companion") {
                                let is_visible = window.is_visible().unwrap_or(false);
                                let is_focused = window.is_focused().unwrap_or(false);

                                // Pressing Alt+J: if visible & focused, hide it; otherwise show and focus
                                if is_visible && is_focused {
                                    let _ = window.hide();
                                } else {
                                    let _ = window.show();
                                    let _ = window.set_focus();
                                }

                                // Emit event to webview for frontend state synchronization
                                let _ = window.emit("jarvis-toggle-shortcut", ());
                            }
                        }
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            show_companion,
            hide_companion,
            toggle_companion
        ])
        .on_window_event(|window, event| {
            // Requirement: Closing the companion must not stop the backend voice assistant
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Prevent window destruction; hide window instead
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            // Register global Alt+J shortcut on setup
            let alt_j: Shortcut = "Alt+J".parse().unwrap();
            let _ = app.global_shortcut().register(alt_j);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running JARVIS desktop companion");
}
