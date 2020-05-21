import React, { Component } from 'react'
import PropTypes from 'prop-types'
import './rating.scss'

const svgStarPoints = "9.9, 1.1, 3.3, 21.78, 19.8, 8.58, 0, 8.58, 16.5, 21.78";

export default class Rating extends Component {
    static propTypes = {
        prop: PropTypes
    }
    inputRef: any

    constructor(props:any) {
        super(props)
        this.state = {
            rating: 0
        }
        
        this.inputRef = React.createRef();
    }

    hanldeClick = (event: any):void =>{
        if(event.target){
            let siblings = event.target;
            let currentFill = event.target.classList;
            if(currentFill.contains("full")){
                currentFill.replace("full","empty");
            }else{
                currentFill.replace("empty","full");
            }
        }
    }

    render() {
        return (
            <div className="rating">
                <svg height="40" width="40">
                <polygon id = "one" className="star empty" onClick={this.hanldeClick} 
                points={svgStarPoints}
                 />
                </svg>
                <svg height="40" width="40">  
                <polygon id = "two" className="star empty" onClick={this.hanldeClick} points={svgStarPoints}
                 /></svg>   
                <svg height="40" width="40">
                <polygon id = "three" className="star empty" onClick={this.hanldeClick} points={svgStarPoints}
                 /></svg>
                <svg height="40" width="40">
                <polygon id = "four" className="star empty" onClick={this.hanldeClick} points={svgStarPoints}
                 /></svg>
                <svg height="40" width="40">
                <polygon id = "five" className="star empty" onClick={this.hanldeClick} points={svgStarPoints}
                 />
                </svg>
            </div>
        )
    }
}
