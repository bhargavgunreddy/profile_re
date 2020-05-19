import React from 'react';
import logo from './logo.svg';
// import { Counter } from './features/counter/Counter';
import './App.css';
import { Header } from './features/header/header';

function App() {
  return (
    <div className="App">
      <header className="App-header">
        <img src={logo} className="App-logo" alt="logo" />
        <Header />
        <p>
          Edit <code>src/App.tsx</code> and save to reload.
        </p>
        
      </header>
    </div>
  );
}

export default App;
