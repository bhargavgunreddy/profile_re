import React from 'react';
import logo from './logo.svg';
// import { Counter } from './features/counter/Counter';
import './App.css';
import { Header } from './features/header/header';

function App() {
  return (
    <div className="App">
      <Header />  
      <img src={logo} className="App-logo" alt="logo" />
        <p>
          Edit <code>src/App.tsx</code> and save to reload.
        </p>
        
    </div>
  );
}

export default App;
