import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FC, FormEvent } from 'react';
import type { FeedbackRecord, UpdateFeedbackInput } from '../types';
import { deleteFeedback, listFeedback, updateFeedback } from '../services/apiClient';
import './FeedbackDashboard.css';

interface FeedbackDashboardProps {
  fastApiUrl?: string;
}

type SearchScope = 'all' | 'transcript' | 'metadata';

interface FeedbackDetailEditorProps {
  record: FeedbackRecord;
  isSaving: boolean;
  onSave: (feedbackId: string, updates: UpdateFeedbackInput) => Promise<void>;
  onDelete: (record: FeedbackRecord) => void;
}

function formatCreatedAt(isoString: string): string {
  try {
    return new Date(isoString).toLocaleString(undefined, {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}

function normalizeTagsInput(rawValue: string): string[] {
  return rawValue
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean);
}

const FeedbackDetailEditor: FC<FeedbackDetailEditorProps> = ({ record, isSaving, onSave, onDelete }) => {
  const [summary, setSummary] = useState<string>(record.summary ?? '');
  const [category, setCategory] = useState<string>(record.category ?? '');
  const [eventName, setEventName] = useState<string>(record.event_name ?? '');
  const [tags, setTags] = useState<string>(Array.isArray(record.tags) ? record.tags.join(', ') : '');

  const resetDraft = () => {
    setSummary(record.summary ?? '');
    setCategory(record.category ?? '');
    setEventName(record.event_name ?? '');
    setTags(Array.isArray(record.tags) ? record.tags.join(', ') : '');
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    await onSave(record.id, {
      summary: summary.trim() ? summary.trim() : null,
      category: category.trim() ? category.trim() : null,
      event_name: eventName.trim() ? eventName.trim() : null,
      tags: normalizeTagsInput(tags),
    });
  };

  return (
    <>
      <header className="feedback-detail-header">
        <div>
          <h3>Feedback Details</h3>
          <p>{formatCreatedAt(record.created_at)}</p>
        </div>
        <button type="button" className="feedback-btn-danger" onClick={() => onDelete(record)}>
          Delete
        </button>
      </header>

      <div className="feedback-detail-body">
        <section className="feedback-readonly-block">
          <h4>Original transcript</h4>
          <div className="feedback-transcript" tabIndex={0} aria-label="Original feedback transcript">
            {record.transcript}
          </div>
        </section>

        <form className="feedback-edit-form" onSubmit={handleSubmit}>
          <h4>Metadata</h4>

          <div className="feedback-form-field">
            <label htmlFor="feedback-summary">Summary</label>
            <textarea
              id="feedback-summary"
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              rows={4}
              placeholder="Optional summary"
            />
          </div>

          <div className="feedback-form-grid">
            <div className="feedback-form-field">
              <label htmlFor="feedback-category">Category</label>
              <input
                id="feedback-category"
                type="text"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                placeholder="Optional category"
              />
            </div>

            <div className="feedback-form-field">
              <label htmlFor="feedback-event-name">Event name</label>
              <input
                id="feedback-event-name"
                type="text"
                value={eventName}
                onChange={(event) => setEventName(event.target.value)}
                placeholder="Optional event or project"
              />
            </div>
          </div>

          <div className="feedback-form-field">
            <label htmlFor="feedback-tags">Tags</label>
            <input
              id="feedback-tags"
              type="text"
              value={tags}
              onChange={(event) => setTags(event.target.value)}
              placeholder="Comma-separated tags"
            />
          </div>

          <div className="feedback-meta-summary">
            <div>
              <span>ID</span>
              <strong>{record.id}</strong>
            </div>
            <div>
              <span>Created</span>
              <strong>{formatCreatedAt(record.created_at)}</strong>
            </div>
          </div>

          <div className="feedback-form-actions">
            <button type="button" className="feedback-btn-secondary" onClick={resetDraft}>
              Reset
            </button>
            <button type="submit" className="feedback-btn-primary" disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save Metadata'}
            </button>
          </div>
        </form>
      </div>
    </>
  );
};

export const FeedbackDashboard: FC<FeedbackDashboardProps> = ({ fastApiUrl = 'http://localhost:8000' }) => {
  const [feedbackItems, setFeedbackItems] = useState<FeedbackRecord[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [searchScope, setSearchScope] = useState<SearchScope>('all');
  const [eventFilter, setEventFilter] = useState<string>('');
  const [categoryFilter, setCategoryFilter] = useState<string>('');
  const [selectedFeedbackId, setSelectedFeedbackId] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<FeedbackRecord | null>(null);
  const statusTimeoutRef = useRef<number | null>(null);

  const sortedFeedback = useMemo(() => {
    return [...feedbackItems].sort((left, right) => right.created_at.localeCompare(left.created_at));
  }, [feedbackItems]);

  const availableEvents = useMemo(() => {
    return Array.from(
      new Set(sortedFeedback.map((item) => item.event_name?.trim()).filter((value): value is string => Boolean(value)))
    ).sort((left, right) => left.localeCompare(right));
  }, [sortedFeedback]);

  const availableCategories = useMemo(() => {
    return Array.from(
      new Set(sortedFeedback.map((item) => item.category?.trim()).filter((value): value is string => Boolean(value)))
    ).sort((left, right) => left.localeCompare(right));
  }, [sortedFeedback]);

  const clearStatus = useCallback(() => {
    setStatusMessage(null);
  }, []);

  const showStatus = useCallback((message: string) => {
    if (statusTimeoutRef.current) {
      window.clearTimeout(statusTimeoutRef.current);
    }
    setStatusMessage(message);
    statusTimeoutRef.current = window.setTimeout(() => setStatusMessage(null), 3500);
  }, []);

  useEffect(() => {
    return () => {
      if (statusTimeoutRef.current) {
        window.clearTimeout(statusTimeoutRef.current);
      }
    };
  }, []);

  const openEditPanel = useCallback((feedback: FeedbackRecord) => {
    setSelectedFeedbackId(feedback.id);
    clearStatus();
    setErrorMessage(null);
  }, [clearStatus]);

  const loadFeedback = useCallback(
    async (preferSelectId?: string) => {
      setIsLoading(true);
      setErrorMessage(null);
      try {
        const response = await listFeedback({ limit: 500 }, fastApiUrl);
        const list = response.feedback || [];
        setFeedbackItems(list);

        setSelectedFeedbackId((current) => {
          if (preferSelectId && list.some((item) => item.id === preferSelectId)) return preferSelectId;
          if (current && list.some((item) => item.id === current)) return current;
          return list[0]?.id ?? null;
        });
      } catch (err: unknown) {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to load feedback records.');
      } finally {
        setIsLoading(false);
      }
    },
    [fastApiUrl]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadFeedback();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [loadFeedback]);

  const filteredFeedback = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return sortedFeedback.filter((item) => {
      if (eventFilter && (item.event_name ?? '') !== eventFilter) return false;
      if (categoryFilter && (item.category ?? '') !== categoryFilter) return false;
      if (!query) return true;

      const haystack = [
        item.transcript,
        item.summary ?? '',
        item.category ?? '',
        item.event_name ?? '',
        Array.isArray(item.tags) ? item.tags.join(' ') : '',
      ]
        .join(' ')
        .toLowerCase();

      if (searchScope === 'transcript') {
        return item.transcript.toLowerCase().includes(query);
      }

      if (searchScope === 'metadata') {
        const metadataHaystack = [item.summary ?? '', item.category ?? '', item.event_name ?? '', Array.isArray(item.tags) ? item.tags.join(' ') : '']
          .join(' ')
          .toLowerCase();
        return metadataHaystack.includes(query);
      }

      return haystack.includes(query);
    });
  }, [categoryFilter, eventFilter, searchQuery, searchScope, sortedFeedback]);

  const selectedRecord = useMemo(() => {
    if (filteredFeedback.length === 0) return null;
    if (selectedFeedbackId) {
      const explicit = filteredFeedback.find((item) => item.id === selectedFeedbackId);
      if (explicit) return explicit;
    }
    return filteredFeedback[0] ?? null;
  }, [filteredFeedback, selectedFeedbackId]);

  const resetFilters = () => {
    setSearchQuery('');
    setSearchScope('all');
    setEventFilter('');
    setCategoryFilter('');
    clearStatus();
    loadFeedback(selectedFeedbackId ?? undefined);
  };

  const refreshFeedback = () => {
    clearStatus();
    loadFeedback(selectedFeedbackId ?? undefined);
  };

  const handleSave = async (feedbackId: string, updates: UpdateFeedbackInput) => {
    if (isSaving) return;

    setIsSaving(true);
    setErrorMessage(null);
    clearStatus();

    try {
      const response = await updateFeedback(feedbackId, updates, fastApiUrl);
      const updated = response.feedback;
      setFeedbackItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setSelectedFeedbackId(updated.id);
      showStatus('Feedback metadata saved.');
      await loadFeedback(updated.id);
    } catch (err: unknown) {
      if (err instanceof Error && /not found/i.test(err.message)) {
        setFeedbackItems((current) => current.filter((item) => item.id !== feedbackId));
        setSelectedFeedbackId((current) => (current === feedbackId ? null : current));
        setErrorMessage('This feedback record was deleted elsewhere and is no longer available.');
        void loadFeedback();
      } else {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to save feedback metadata.');
      }
    } finally {
      setIsSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteCandidate || isDeleting) return;

    setIsDeleting(true);
    setErrorMessage(null);
    clearStatus();
    try {
      await deleteFeedback(deleteCandidate.id, fastApiUrl);
      setFeedbackItems((current) => current.filter((item) => item.id !== deleteCandidate.id));
      if (selectedFeedbackId === deleteCandidate.id) {
        setSelectedFeedbackId(null);
      }
      setDeleteCandidate(null);
      showStatus('Feedback record deleted.');
      await loadFeedback();
    } catch (err: unknown) {
      if (err instanceof Error && /not found/i.test(err.message)) {
        setFeedbackItems((current) => current.filter((item) => item.id !== deleteCandidate.id));
        if (selectedFeedbackId === deleteCandidate.id) {
          setSelectedFeedbackId(null);
        }
        setDeleteCandidate(null);
        setErrorMessage('This feedback record was already deleted. The list has been refreshed.');
        void loadFeedback();
        return;
      }
      setErrorMessage(err instanceof Error ? err.message : 'Failed to delete feedback record.');
    } finally {
      setIsDeleting(false);
    }
  };

  const hasActiveFilters = Boolean(searchQuery.trim() || eventFilter || categoryFilter || searchScope !== 'all');

  return (
    <div className="feedback-dashboard-layout">
      <header className="feedback-hud-header">
        <div className="feedback-hud-title-area">
          <h2>FEEDBACK DASHBOARD</h2>
          <div className="feedback-hud-subtitle">LOCAL FEEDBACK STORE • FASTAPI BACKEND • METADATA-ONLY EDITING</div>
        </div>

        <div className="feedback-hud-controls">
          <button type="button" className="feedback-btn-secondary" onClick={refreshFeedback}>
            Refresh
          </button>
          <button type="button" className="feedback-btn-secondary" onClick={resetFilters} disabled={!hasActiveFilters}>
            Clear Filters
          </button>
        </div>
      </header>

      {statusMessage && (
        <div className="feedback-banner feedback-banner-success" role="status" aria-live="polite">
          {statusMessage}
        </div>
      )}

      {errorMessage && (
        <div className="feedback-banner feedback-banner-error" role="alert">
          <span>{errorMessage}</span>
          <button type="button" className="feedback-banner-action" onClick={refreshFeedback}>
            Retry
          </button>
        </div>
      )}

      <section className="feedback-toolbar" aria-label="Feedback filters">
        <div className="feedback-search-group">
          <label className="feedback-field-label" htmlFor="feedback-search-input">
            Search
          </label>
          <input
            id="feedback-search-input"
            type="search"
            className="feedback-search-input"
            placeholder="Search transcript, summary, category, event, or tags"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            aria-label="Search feedback records"
          />
        </div>

        <div className="feedback-filter-row">
          <div className="feedback-field-group">
            <label className="feedback-field-label" htmlFor="feedback-search-scope">
              Search scope
            </label>
            <select
              id="feedback-search-scope"
              className="feedback-select"
              value={searchScope}
              onChange={(event) => setSearchScope(event.target.value as SearchScope)}
            >
              <option value="all">All fields</option>
              <option value="transcript">Transcript only</option>
              <option value="metadata">Metadata only</option>
            </select>
          </div>

          <div className="feedback-field-group">
            <label className="feedback-field-label" htmlFor="feedback-event-filter">
              Event name
            </label>
            <select
              id="feedback-event-filter"
              className="feedback-select"
              value={eventFilter}
              onChange={(event) => setEventFilter(event.target.value)}
            >
              <option value="">All events</option>
              {availableEvents.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>

          <div className="feedback-field-group">
            <label className="feedback-field-label" htmlFor="feedback-category-filter">
              Category
            </label>
            <select
              id="feedback-category-filter"
              className="feedback-select"
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
            >
              <option value="">All categories</option>
              {availableCategories.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      <section className="feedback-content-grid">
        <aside className="feedback-list-panel" aria-label="Saved feedback records">
          <div className="feedback-list-header">
            <div>
              <h3>Saved Feedback</h3>
              <p>{filteredFeedback.length} visible record(s)</p>
            </div>
          </div>

          <div className="feedback-list-scroll">
            {isLoading ? (
              <div className="feedback-state">Loading feedback records...</div>
            ) : sortedFeedback.length === 0 ? (
              <div className="feedback-state">
                <h4>No feedback yet</h4>
                <p>Saved feedback records will appear here once they are captured by the backend.</p>
              </div>
            ) : filteredFeedback.length === 0 ? (
              <div className="feedback-state">
                <h4>No matches found</h4>
                <p>Try widening the search terms or clearing the current filters.</p>
              </div>
            ) : (
              filteredFeedback.map((item) => {
                const isActive = item.id === selectedRecord?.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`feedback-list-item ${isActive ? 'active' : ''}`}
                    onClick={() => openEditPanel(item)}
                  >
                    <div className="feedback-list-item-header">
                      <span className="feedback-list-item-id">{item.id}</span>
                      <span className="feedback-list-item-date">{formatCreatedAt(item.created_at)}</span>
                    </div>
                    <div className="feedback-list-item-preview">{item.transcript}</div>
                    <div className="feedback-chip-row">
                      {item.event_name && <span className="feedback-chip">Event: {item.event_name}</span>}
                      {item.category && <span className="feedback-chip">Category: {item.category}</span>}
                      {Array.isArray(item.tags) && item.tags.length > 0 && (
                        <span className="feedback-chip">Tags: {item.tags.join(', ')}</span>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <section className="feedback-detail-panel" aria-label="Feedback details">
          {!selectedRecord ? (
            <div className="feedback-state feedback-detail-empty">
              <h3>Select a feedback record</h3>
              <p>Choose a record from the list to inspect its transcript and update metadata.</p>
            </div>
          ) : (
            <>
              <FeedbackDetailEditor
                key={selectedRecord.id}
                record={selectedRecord}
                isSaving={isSaving}
                onSave={handleSave}
                onDelete={setDeleteCandidate}
              />
            </>
          )}
        </section>
      </section>

      {deleteCandidate && (
        <div className="feedback-modal-overlay" role="presentation" onClick={() => !isDeleting && setDeleteCandidate(null)}>
          <div className="feedback-modal-card" role="dialog" aria-modal="true" aria-labelledby="feedback-delete-title" onClick={(event) => event.stopPropagation()}>
            <h3 id="feedback-delete-title">Confirm deletion</h3>
            <p>
              Delete this feedback record permanently? The original transcript and its metadata will be removed from the local store.
            </p>
            <div className="feedback-modal-actions">
              <button type="button" className="feedback-btn-secondary" onClick={() => setDeleteCandidate(null)} disabled={isDeleting}>
                Cancel
              </button>
              <button type="button" className="feedback-btn-danger" onClick={confirmDelete} disabled={isDeleting}>
                {isDeleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};