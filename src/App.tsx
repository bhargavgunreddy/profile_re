import React, { useState } from 'react';
import logo from './logo.svg';
// import { Counter } from './features/counter/Counter';
import './App.scss';
import { Header } from './features/header/header';
import Rating from './features/rating/rating';
import ThemeSwitch from './features/theme/themeSwitch';

function App() {
  
  const [state, setState] = useState({'theme':'day'});

  const handleThemeChange = (params:any) => {
    setState({'theme': params});

  }
  return (
    <div className="App">
      <ThemeSwitch/>
      <Rating />  
      <img src={logo} className="App-logo" alt="logo" />
        <p>
          Edit <code>src/App.tsx</code> and save to reload.
        </p>
        
    </div>
  );
}

export default App;
