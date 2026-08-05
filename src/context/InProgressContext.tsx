import { createContext, useContext, useState, useCallback, ReactNode } from 'react';

export interface InProgressItem {
  fileId: string | null;
  inputType: 'image' | 'audio' | 'text' | null;
  url: string | null;
  filename: string | null;
  prompt: string | null;
  saved?: boolean;
}

interface InProgressContextValue {
  item: InProgressItem | null;
  setItem: (item: InProgressItem) => void;
  updatePrompt: (prompt: string) => void;
  markSaved: () => void;
  clearItem: () => void;
}

const STORAGE_KEY = 'imagesound_in_progress';

function readStorage(): InProgressItem | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as InProgressItem) : null;
  } catch {
    return null;
  }
}

function writeStorage(item: InProgressItem | null): void {
  try {
    if (item === null) sessionStorage.removeItem(STORAGE_KEY);
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify(item));
  } catch { /* sessionStorage unavailable — in-memory only */ }
}

const InProgressContext = createContext<InProgressContextValue | null>(null);

export function InProgressProvider({ children }: { children: ReactNode }) {
  const [item, setItemState] = useState<InProgressItem | null>(() => readStorage());

  const setItem = useCallback((next: InProgressItem) => {
    setItemState(next);
    writeStorage(next);
  }, []);

  const updatePrompt = useCallback((prompt: string) => {
    setItemState(prev => {
      if (!prev) return prev;
      const next = { ...prev, prompt };
      writeStorage(next);
      return next;
    });
  }, []);

  const markSaved = useCallback(() => {
    setItemState(prev => {
      if (!prev) return prev;
      const next = { ...prev, saved: true };
      writeStorage(next);
      return next;
    });
  }, []);

  const clearItem = useCallback(() => {
    setItemState(null);
    writeStorage(null);
  }, []);

  return (
    <InProgressContext.Provider value={{ item, setItem, updatePrompt, markSaved, clearItem }}>
      {children}
    </InProgressContext.Provider>
  );
}

export function useInProgress(): InProgressContextValue {
  const ctx = useContext(InProgressContext);
  if (!ctx) throw new Error('useInProgress must be used inside InProgressProvider');
  return ctx;
}
