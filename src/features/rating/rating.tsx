import React, { Component } from 'react'
import PropTypes from 'prop-types'
import './rating.scss'
import { start } from 'repl';

const svgStarPoints = "9.9, 1.1, 3.3, 21.78, 19.8, 8.58, 0, 8.58, 16.5, 21.78";

interface RatingState{
    rating: number;
    ratingSet: boolean
}

interface RatingsProps{
}

export default class Rating extends Component<RatingsProps, RatingState> {
    static propTypes = {
        prop: PropTypes
    }
    inputRef: any

    constructor(props:any) {
        super(props)
        this.state = {
            rating: 0,
            ratingSet: false
        }
        
        this.inputRef = React.createRef();
    }

    handleClick = (event: any):void =>{
        if(event.target){
            let parentRef = event.currentTarget;
            let updatedRating = 0;
            
            while(parentRef && !this.state.ratingSet){
                parentRef.firstElementChild.classList.add("full");
                parentRef.firstElementChild.classList.remove("empty");
                parentRef = parentRef.nextSibling;
                updatedRating += 1
            }
            if(!this.state.ratingSet){
                this.setState({
                    rating: updatedRating,
                    ratingSet: true
                });
            }
        }
    }

    handleFocus = (event: any):void =>{
        let parentRef:any = event.target.parentElement;
        let updatedRating = 0;
        while(parentRef && !this.state.ratingSet){
            parentRef.firstElementChild?.classList.replace("empty", "full");
                parentRef = parentRef.nextSibling;
                updatedRating += 1
                this.setState({
                    rating: updatedRating
                });
        }
    }

    handleBlur = (event: any):void =>{
        let parentRef:any = event.target.parentElement;
        let updatedRating = this.state.rating;
        while(parentRef&& !this.state.ratingSet){
            parentRef.firstElementChild?.classList.replace("full", "empty");
                parentRef = parentRef.nextSibling;
                updatedRating -= 1;
                this.setState({
                    rating: updatedRating
                });
        }
    }

    handleReset = ()=>{

        let childArr = this.inputRef.current.children;
        Array.from(childArr).forEach((item: any)=>{
           item.firstElementChild.classList.replace("full", "empty");
        })
        this.setState({
            rating: 0,
            ratingSet: false
        })
    }

    render() {
        return (
            <React.Fragment>
            <div className="rating" ref={this.inputRef}>
                
                <svg height="30" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "one" className="star empty" 
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>
                <svg height="30" width="40" className="svg-icon" onClick={this.handleClick}>  
                    <polygon id = "two" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>   
                <svg height="30" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "three" className="star empty" 
                onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                points={svgStarPoints}
                 /></svg>
                <svg height="30" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "four" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}
                 /></svg>
                <svg height="30" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "five" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>
            </div>
            <div>
                    Rating : {this.state.rating}
            </div>
            <div>
                <button onClick={this.handleReset}>Reset</button>
            </div>
            </React.Fragment>
        )
    }
}
