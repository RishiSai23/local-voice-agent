import React, { useState, useEffect, useCallback, useMemo } from 'react';
import type { FC } from 'react';
import type { Task, TaskStatus, TaskRecurrence, CreateTaskInput, NotificationSettings } from '../types';
import {
  listTasks,
  createTask,
  updateTask,
  completeTask,
  cancelTask,
  deleteTask,
  snoozeReminder,
  dismissReminder,
  listUpcomingReminders,
  getNotificationSettings,
  updateNotificationSettings,
  triggerSchedulerCheck,
  testNotification,
} from '../services/apiClient';
import './TasksDashboard.css';

interface TasksDashboardProps {
  fastApiUrl?: string;
}

export const TasksDashboard: FC<TasksDashboardProps> = ({ fastApiUrl = 'http://localhost:8000' }) => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [upcomingReminders, setUpcomingReminders] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<'all' | 'pending' | 'completed' | 'cancelled'>('pending');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Notification Preferences
  const [notificationSettings, setNotificationSettings] = useState<NotificationSettings>({
    windows_notifications_enabled: true,
    spoken_reminders_enabled: true,
  });

  // Modal State for Create / Edit
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [formTitle, setFormTitle] = useState<string>('');
  const [formDesc, setFormDesc] = useState<string>('');
  const [formDueDate, setFormDueDate] = useState<string>('');
  const [formDueTime, setFormDueTime] = useState<string>('');
  const [formReminderDate, setFormReminderDate] = useState<string>('');
  const [formReminderTime, setFormReminderTime] = useState<string>('');
  const [formRecurrence, setFormRecurrence] = useState<TaskRecurrence>(null);

  // Delete Confirmation State
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  // Feedback Toast Banner
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);

  const showFeedback = (msg: string) => {
    setFeedbackMessage(msg);
    setTimeout(() => setFeedbackMessage(null), 3500);
  };

  // Format local date / time strings
  const formatDateTime = (isoString?: string | null) => {
    if (!isoString) return 'None';
    try {
      const d = new Date(isoString);
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  // Fetch all tasks and upcoming reminders
  const refreshData = useCallback(async () => {
    try {
      setErrorMessage(null);
      const [tasksRes, remRes, notifRes] = await Promise.all([
        listTasks({}, fastApiUrl),
        listUpcomingReminders({ window_hours: 72 }, fastApiUrl),
        getNotificationSettings(fastApiUrl),
      ]);
      setTasks(tasksRes.tasks || []);
      setUpcomingReminders(remRes.reminders || []);
      if (notifRes && notifRes.settings) {
        setNotificationSettings(notifRes.settings);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to connect to task engine.';
      setErrorMessage(msg);
    } finally {
      setIsLoading(false);
    }
  }, [fastApiUrl]);

  // Initial load and periodic poll
  useEffect(() => {
    setIsLoading(true);
    refreshData();
    const interval = setInterval(refreshData, 5000);
    return () => clearInterval(interval);
  }, [refreshData]);

  // Open Create Modal
  const handleOpenCreateModal = () => {
    setEditingTask(null);
    setFormTitle('');
    setFormDesc('');
    setFormDueDate('');
    setFormDueTime('');
    setFormReminderDate('');
    setFormReminderTime('');
    setFormRecurrence(null);
    setIsModalOpen(true);
  };

  // Open Edit Modal
  const handleOpenEditModal = (task: Task) => {
    setEditingTask(task);
    setFormTitle(task.title);
    setFormDesc(task.description || '');

    if (task.due_at) {
      const d = new Date(task.due_at);
      setFormDueDate(d.toISOString().slice(0, 10));
      setFormDueTime(d.toTimeString().slice(0, 5));
    } else {
      setFormDueDate('');
      setFormDueTime('');
    }

    if (task.reminder_at) {
      const d = new Date(task.reminder_at);
      setFormReminderDate(d.toISOString().slice(0, 10));
      setFormReminderTime(d.toTimeString().slice(0, 5));
    } else {
      setFormReminderDate('');
      setFormReminderTime('');
    }

    setFormRecurrence(task.recurrence);
    setIsModalOpen(true);
  };

  // Submit Create or Edit Form
  const handleSubmitForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim()) {
      alert('Task title is required.');
      return;
    }

    // Build ISO datetime strings if dates are set
    let dueIso: string | undefined = undefined;
    if (formDueDate) {
      const timePart = formDueTime ? formDueTime : '09:00';
      dueIso = new Date(`${formDueDate}T${timePart}:00`).toISOString();
    }

    let reminderIso: string | undefined = undefined;
    if (formReminderDate) {
      const timePart = formReminderTime ? formReminderTime : '09:00';
      reminderIso = new Date(`${formReminderDate}T${timePart}:00`).toISOString();
    }

    try {
      if (editingTask) {
        await updateTask(
          editingTask.id,
          {
            title: formTitle.trim(),
            description: formDesc.trim() || undefined,
            due_at: dueIso || undefined,
            reminder_at: reminderIso || undefined,
            recurrence: formRecurrence,
          },
          fastApiUrl
        );
        showFeedback(`Task '${formTitle}' updated.`);
      } else {
        const input: CreateTaskInput = {
          title: formTitle.trim(),
          description: formDesc.trim() || undefined,
          due_at: dueIso,
          reminder_at: reminderIso,
          recurrence: formRecurrence,
        };
        await createTask(input, fastApiUrl);
        showFeedback(`Task '${formTitle}' created.`);
      }
      setIsModalOpen(false);
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error saving task.');
    }
  };

  // Quick Actions: Complete, Cancel, Delete, Snooze, Dismiss
  const handleComplete = async (taskId: string, title: string) => {
    try {
      await completeTask(taskId, fastApiUrl);
      showFeedback(`Marked '${title}' as completed.`);
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error completing task.');
    }
  };

  const handleCancel = async (taskId: string, title: string) => {
    try {
      await cancelTask(taskId, fastApiUrl);
      showFeedback(`Cancelled '${title}'.`);
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error cancelling task.');
    }
  };

  const handleDelete = async (taskId: string) => {
    try {
      await deleteTask(taskId, fastApiUrl);
      setDeletingTaskId(null);
      showFeedback('Task deleted successfully.');
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error deleting task.');
    }
  };

  const handleSnooze = async (taskId: string, minutes: number) => {
    try {
      await snoozeReminder(taskId, minutes, fastApiUrl);
      showFeedback(`Snoozed reminder for ${minutes} minutes.`);
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error snoozing reminder.');
    }
  };

  const handleDismiss = async (taskId: string) => {
    try {
      await dismissReminder(taskId, fastApiUrl);
      showFeedback('Reminder dismissed.');
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error dismissing reminder.');
    }
  };

  const handleToggleNotifications = async (field: 'windows_notifications_enabled' | 'spoken_reminders_enabled') => {
    try {
      const newVal = !notificationSettings[field];
      const res = await updateNotificationSettings({ [field]: newVal }, fastApiUrl);
      setNotificationSettings(res.settings);
      showFeedback(`Notification settings updated.`);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error updating settings.');
    }
  };

  const handleTriggerScheduler = async () => {
    try {
      const res = await triggerSchedulerCheck(fastApiUrl);
      showFeedback(`Scheduler checked. ${res.claimed_count} due reminder(s) claimed.`);
      refreshData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error triggering scheduler.');
    }
  };

  const handleTestNotification = async () => {
    try {
      const res = await testNotification(
        {
          title: 'J.A.R.V.I.S. Alert Test',
          message: 'Windows desktop notification pipeline verified successfully.',
          test_speech: false,
        },
        fastApiUrl
      );
      if (res.status === 'ok') {
        const method = res.notification?.method || 'success';
        showFeedback(`Notification triggered successfully via ${method}!`);
      } else {
        const errs = res.notification?.errors?.join(', ') || 'Blocked by Windows policy';
        alert(`Notification test result: ${errs}`);
      }
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Error testing notification.');
    }
  };

  // Filter and search tasks
  const filteredTasks = useMemo(() => {
    return tasks.filter((t) => {
      if (activeFilter !== 'all' && t.status !== activeFilter) {
        return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchTitle = t.title.toLowerCase().includes(q);
        const matchDesc = t.description ? t.description.toLowerCase().includes(q) : false;
        return matchTitle || matchDesc;
      }
      return true;
    });
  }, [tasks, activeFilter, searchQuery]);

  return (
    <div className="tasks-dashboard-layout">
      {/* Top HUD Header */}
      <div className="tasks-hud-header">
        <div className="tasks-hud-title-area">
          <h2>
            <span>📋</span> TASK ENGINE & REMINDER SCHEDULER
          </h2>
          <div className="tasks-hud-subtitle">
            LOCAL-FIRST SCHEDULING • DURABLE SQLITE PERSISTENCE • WINDOWS TOAST ALERTS
          </div>
        </div>

        <div className="tasks-hud-controls">
          <button
            type="button"
            className="hud-btn-secondary"
            onClick={handleTestNotification}
            title="Trigger an immediate diagnostic Windows desktop notification"
          >
            🔔 Test Alert
          </button>
          <button
            type="button"
            className="hud-btn-secondary"
            onClick={handleTriggerScheduler}
            title="Force an immediate background check for due reminders"
          >
            ⚡ Check Due Now
          </button>
          <button
            type="button"
            className="hud-btn-primary"
            onClick={handleOpenCreateModal}
          >
            + New Task / Reminder
          </button>
        </div>
      </div>

      {/* Feedback Banner */}
      {feedbackMessage && (
        <div
          style={{
            background: 'rgba(16, 185, 129, 0.15)',
            border: '1px solid rgba(16, 185, 129, 0.4)',
            color: '#10b981',
            padding: '8px 16px',
            borderRadius: '6px',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
          }}
        >
          ✓ {feedbackMessage}
        </div>
      )}

      {/* Error Alert */}
      {errorMessage && (
        <div
          style={{
            background: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            color: '#ef4444',
            padding: '10px 16px',
            borderRadius: '6px',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
          }}
        >
          ⚠️ {errorMessage}
        </div>
      )}

      {/* Upcoming Reminders Alert Strip */}
      {upcomingReminders.length > 0 && (
        <div className="upcoming-reminders-banner">
          <div className="upcoming-banner-title">
            <span>⏰ UPCOMING REMINDERS ({upcomingReminders.length})</span>
            <span>AUTO-DISPATCH ACTIVE</span>
          </div>
          <div className="upcoming-items-grid">
            {upcomingReminders.map((rem) => (
              <div key={rem.id} className="upcoming-card">
                <div className="upcoming-card-info">
                  <span className="upcoming-card-title">{rem.title}</span>
                  <span className="upcoming-card-time">
                    {formatDateTime(rem.reminder_at)}
                    {rem.recurrence ? ` [${rem.recurrence.toUpperCase()}]` : ''}
                  </span>
                </div>
                <div className="upcoming-card-actions">
                  <button
                    type="button"
                    className="mini-action-btn"
                    onClick={() => handleSnooze(rem.id, 10)}
                    title="Snooze for 10 minutes"
                  >
                    +10m
                  </button>
                  <button
                    type="button"
                    className="mini-action-btn"
                    onClick={() => handleDismiss(rem.id)}
                    title="Dismiss reminder"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filter and Notification Settings Bar */}
      <div className="tasks-filter-bar">
        <div className="tasks-status-tabs" role="tablist">
          <button
            type="button"
            className={`status-tab ${activeFilter === 'pending' ? 'active' : ''}`}
            onClick={() => setActiveFilter('pending')}
          >
            Pending ({tasks.filter((t) => t.status === 'pending').length})
          </button>
          <button
            type="button"
            className={`status-tab ${activeFilter === 'all' ? 'active' : ''}`}
            onClick={() => setActiveFilter('all')}
          >
            All ({tasks.length})
          </button>
          <button
            type="button"
            className={`status-tab ${activeFilter === 'completed' ? 'active' : ''}`}
            onClick={() => setActiveFilter('completed')}
          >
            Completed ({tasks.filter((t) => t.status === 'completed').length})
          </button>
          <button
            type="button"
            className={`status-tab ${activeFilter === 'cancelled' ? 'active' : ''}`}
            onClick={() => setActiveFilter('cancelled')}
          >
            Cancelled ({tasks.filter((t) => t.status === 'cancelled').length})
          </button>
        </div>

        <input
          type="text"
          className="tasks-search-input"
          placeholder="Search tasks..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />

        {/* Quick Notification Settings Toggles */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
          <button
            type="button"
            onClick={() => handleToggleNotifications('windows_notifications_enabled')}
            className={`mini-action-btn ${notificationSettings.windows_notifications_enabled ? 'active' : ''}`}
            style={{
              borderColor: notificationSettings.windows_notifications_enabled ? '#00f0ff' : 'rgba(255,255,255,0.2)',
              color: notificationSettings.windows_notifications_enabled ? '#00f0ff' : 'var(--text-muted)',
            }}
            title="Enable or disable Windows desktop toast alerts"
          >
            🪟 Windows Toast: {notificationSettings.windows_notifications_enabled ? 'ON' : 'OFF'}
          </button>

          <button
            type="button"
            onClick={() => handleToggleNotifications('spoken_reminders_enabled')}
            className={`mini-action-btn ${notificationSettings.spoken_reminders_enabled ? 'active' : ''}`}
            style={{
              borderColor: notificationSettings.spoken_reminders_enabled ? '#00f0ff' : 'rgba(255,255,255,0.2)',
              color: notificationSettings.spoken_reminders_enabled ? '#00f0ff' : 'var(--text-muted)',
            }}
            title="Enable or disable spoken voice reminders via Piper TTS"
          >
            🗣️ Voice Alerts: {notificationSettings.spoken_reminders_enabled ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {/* Task List Grid */}
      {isLoading ? (
        <div className="tasks-empty-state">
          <div className="dot" style={{ animation: 'beaconPulse 1.5s infinite' }} />
          <span>Synchronizing task matrix with SQLite...</span>
        </div>
      ) : filteredTasks.length === 0 ? (
        <div className="tasks-empty-state">
          <h4>No tasks found</h4>
          <p>
            {activeFilter === 'pending'
              ? 'You have no pending tasks. Speak to J.A.R.V.I.S. or click "+ New Task" above to create one.'
              : 'No tasks match the selected filter.'}
          </p>
        </div>
      ) : (
        <div className="tasks-list-grid">
          {filteredTasks.map((task) => (
            <div key={task.id} className={`task-card ${task.status}`}>
              <div className="task-card-header">
                <h3 className="task-card-title">{task.title}</h3>
                <span className={`task-card-badge badge-${task.status}`}>{task.status}</span>
              </div>

              {task.description && <p className="task-card-desc">{task.description}</p>}

              <div className="task-card-meta">
                {task.due_at && (
                  <div className="task-meta-item meta-due">
                    <span>📅 Due:</span>
                    <span>{formatDateTime(task.due_at)}</span>
                  </div>
                )}
                {task.reminder_at && (
                  <div className="task-meta-item meta-reminder">
                    <span>⏰ Reminder:</span>
                    <span>{formatDateTime(task.reminder_at)}</span>
                  </div>
                )}
                {task.recurrence && (
                  <div className="task-meta-item meta-recurrence">
                    <span>🔄 {task.recurrence}</span>
                  </div>
                )}
              </div>

              <div className="task-card-actions">
                <div className="card-action-group">
                  {task.status === 'pending' && (
                    <>
                      <button
                        type="button"
                        className="mini-action-btn"
                        onClick={() => handleComplete(task.id, task.title)}
                        title="Mark Completed"
                        style={{ color: '#10b981', borderColor: 'rgba(16, 185, 129, 0.4)' }}
                      >
                        ✓ Done
                      </button>
                      {task.reminder_at && (
                        <button
                          type="button"
                          className="mini-action-btn"
                          onClick={() => handleSnooze(task.id, 10)}
                          title="Snooze reminder by 10m"
                        >
                          +10m
                        </button>
                      )}
                      <button
                        type="button"
                        className="mini-action-btn"
                        onClick={() => handleCancel(task.id, task.title)}
                        title="Cancel Task"
                        style={{ color: '#ef4444' }}
                      >
                        Cancel
                      </button>
                    </>
                  )}
                </div>

                <div className="card-action-group">
                  <button
                    type="button"
                    className="mini-action-btn"
                    onClick={() => handleOpenEditModal(task)}
                    title="Edit Task"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="mini-action-btn"
                    onClick={() => setDeletingTaskId(task.id)}
                    title="Delete Task"
                    style={{ color: '#ef4444' }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Delete Confirmation Safeguard Modal */}
      {deletingTaskId && (
        <div className="tasks-modal-overlay">
          <div className="tasks-modal-card" style={{ maxWidth: '420px' }}>
            <div className="tasks-modal-header">
              <h3 style={{ color: '#ef4444' }}>⚠️ Confirm Task Deletion</h3>
            </div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              Are you sure you want to permanently delete this task and its reminder history? This operation cannot be undone.
            </p>
            <div className="tasks-modal-footer">
              <button
                type="button"
                className="hud-btn-secondary"
                onClick={() => setDeletingTaskId(null)}
              >
                Keep Task
              </button>
              <button
                type="button"
                className="hud-btn-primary"
                style={{ background: 'rgba(239, 68, 68, 0.2)', borderColor: '#ef4444', color: '#ef4444' }}
                onClick={() => handleDelete(deletingTaskId)}
              >
                Delete Permanently
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create / Edit Modal Form */}
      {isModalOpen && (
        <div className="tasks-modal-overlay" onClick={() => setIsModalOpen(false)}>
          <div className="tasks-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="tasks-modal-header">
              <h3>{editingTask ? 'EDIT TASK / REMINDER' : 'CREATE NEW TASK / REMINDER'}</h3>
              <button
                type="button"
                className="mini-action-btn"
                onClick={() => setIsModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSubmitForm} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div className="tasks-form-field">
                <label>Task Title *</label>
                <input
                  type="text"
                  placeholder="e.g. Submit homework assignment"
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  required
                  autoFocus
                />
              </div>

              <div className="tasks-form-field">
                <label>Description / Notes</label>
                <textarea
                  placeholder="Optional details or instructions..."
                  rows={2}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                />
              </div>

              <div className="tasks-form-row">
                <div className="tasks-form-field">
                  <label>Due Date</label>
                  <input
                    type="date"
                    value={formDueDate}
                    onChange={(e) => setFormDueDate(e.target.value)}
                  />
                </div>
                <div className="tasks-form-field">
                  <label>Due Time</label>
                  <input
                    type="time"
                    value={formDueTime}
                    onChange={(e) => setFormDueTime(e.target.value)}
                  />
                </div>
              </div>

              <div className="tasks-form-row">
                <div className="tasks-form-field">
                  <label>Reminder Date</label>
                  <input
                    type="date"
                    value={formReminderDate}
                    onChange={(e) => setFormReminderDate(e.target.value)}
                  />
                </div>
                <div className="tasks-form-field">
                  <label>Reminder Time</label>
                  <input
                    type="time"
                    value={formReminderTime}
                    onChange={(e) => setFormReminderTime(e.target.value)}
                  />
                </div>
              </div>

              <div className="tasks-form-field">
                <label>Recurrence Rule</label>
                <select
                  value={formRecurrence || ''}
                  onChange={(e) => setFormRecurrence((e.target.value as TaskRecurrence) || null)}
                >
                  <option value="">One-Time (No Recurrence)</option>
                  <option value="daily">Daily (Every Day)</option>
                  <option value="weekday">Weekday (Monday – Friday)</option>
                  <option value="weekly">Weekly (Every 7 Days)</option>
                </select>
              </div>

              <div className="tasks-modal-footer">
                <button
                  type="button"
                  className="hud-btn-secondary"
                  onClick={() => setIsModalOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="hud-btn-primary"
                >
                  {editingTask ? 'Save Changes' : 'Create Task'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
