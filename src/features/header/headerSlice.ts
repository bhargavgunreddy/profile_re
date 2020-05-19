import { createSlice } from "@reduxjs/toolkit";

interface HeaderState {
    theme: string;
  }

const initialState = {
     theme: 'light'
    }


const headerSlice = createSlice({
    name: 'headerSlice',
    initialState,
    reducers: {
        updateTheme(state, action){
            const currentTheme = action.payload;
            state.theme =  currentTheme;
        }
    }
})

export const { updateTheme} = headerSlice.actions;

export default headerSlice.reducer;