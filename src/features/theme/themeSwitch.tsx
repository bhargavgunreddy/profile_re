import React, { Component } from 'react'
import './theme.scss';

export default class ThemeSwitch extends Component {
    constructor(props: Props){
        super(props);
    }
    
    themeNight = () => {
        console.log("clicked 1");

        let appRef: any= document.querySelector('#root');
        appRef.setAttribute('data-theme', 'night');
    }

    themeDay = () => {
        console.log("clicked 2");

        let appRef: any= document.querySelector('#root');
        appRef.removeAttribute('data-theme');
    }

    handleChange = () => {
        console.log("clicked");
    }
    
    render() {
        return (
            <div className='theme'>
                <button onClick = {this.themeDay} className ="theme-switch-btn">DAY</button>
                <button onClick = {this.themeNight} className ="theme-switch-btn">NIGHT</button>
            </div>
        )
    }
}

export interface Props {
    onChange: any;
  }
