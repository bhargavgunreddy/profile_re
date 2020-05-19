import { configureStore, ThunkAction, Action } from '@reduxjs/toolkit';
import { combineReducers } from 'redux'
import counterReducer from '../features/counter/counterSlice';

import headerReducer from '../features/header/headerSlice';

const reducersRef = combineReducers({
    header: headerReducer,
   counter:  counterReducer
  })

export const store = configureStore({
  reducer: reducersRef
});

export type RootState = ReturnType<typeof store.getState>;
export type AppThunk<ReturnType = void> = ThunkAction<
  ReturnType,
  RootState,
  unknown,
  Action<string>
>;
