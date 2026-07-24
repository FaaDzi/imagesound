import { useReducer, useCallback } from 'react';

type State = {
  draftText: string;
  checkpoints: string[];
  index: number;
};

type Action =
  | { type: 'INITIALIZE'; text: string }
  | { type: 'SET_DRAFT'; text: string }
  | { type: 'COMMIT' }
  | { type: 'STEP_BACK' }
  | { type: 'STEP_FORWARD' }
  | { type: 'REVERT' };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'INITIALIZE':
      return { draftText: action.text, checkpoints: [action.text], index: 0 };
    case 'SET_DRAFT':
      return { ...state, draftText: action.text };
    case 'COMMIT': {
      const { draftText, checkpoints, index } = state;
      if (!draftText.trim()) return state;
      if (draftText === checkpoints[index]) return state;
      const next = [...checkpoints.slice(0, index + 1), draftText];
      return { ...state, checkpoints: next, index: next.length - 1 };
    }
    case 'STEP_BACK': {
      if (state.index <= 0) return state;
      const i = state.index - 1;
      return { ...state, index: i, draftText: state.checkpoints[i] };
    }
    case 'STEP_FORWARD': {
      if (state.index >= state.checkpoints.length - 1) return state;
      const i = state.index + 1;
      return { ...state, index: i, draftText: state.checkpoints[i] };
    }
    case 'REVERT':
      if (state.checkpoints.length === 0) return state;
      return { ...state, index: 0, draftText: state.checkpoints[0] };
    default:
      return state;
  }
}

export function usePromptHistory() {
  const [state, dispatch] = useReducer(reducer, {
    draftText: '',
    checkpoints: [],
    index: 0,
  });

  const initialize = useCallback((text: string) => dispatch({ type: 'INITIALIZE', text }), []);
  const setDraftText = useCallback((text: string) => dispatch({ type: 'SET_DRAFT', text }), []);
  const commit = useCallback(() => dispatch({ type: 'COMMIT' }), []);
  const stepBack = useCallback(() => dispatch({ type: 'STEP_BACK' }), []);
  const stepForward = useCallback(() => dispatch({ type: 'STEP_FORWARD' }), []);
  const revertToOriginal = useCallback(() => dispatch({ type: 'REVERT' }), []);

  return {
    draftText: state.draftText,
    checkpoints: state.checkpoints,
    index: state.index,
    canStepBack: state.index > 0,
    canStepForward: state.index < state.checkpoints.length - 1,
    initialize,
    setDraftText,
    commit,
    stepBack,
    stepForward,
    revertToOriginal,
  };
}
